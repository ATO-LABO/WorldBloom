const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const src=fs.readFileSync(require.resolve('../viewer/static/creation-workflow.js'),'utf8');
const fields={};class E{constructor(){this.hidden=false;this.disabled=false;this.dataset={};this.value='';this.checked=false;this.elements=[];}setAttribute(k,v){this[k]=v;}}
const $=key=>fields[key]||(fields[key]=new E());
const S={screen:'workflow',active:true,step:1,busy:false,unknown:false,conflict:false,kind:'new',native:null,saved:null,checked:null,published:null,aiId:null,aiCancelled:false,ai:null,revision:null,draft:null,pending:null};
const footer=new E();const context={S,$,refinement:null,root:{dataset:{mode:'waiting'},querySelectorAll:()=>Object.values(fields),querySelector:()=>footer},sourcePicker:{sync:()=>{}},initialGeneration:{active:()=>false},listBusy:false,resuming:false,dirty:()=>context.isDirty,semantic:()=> 'signature',savedStage:()=> '① 下書きを用意',publishedNative:()=>S.kind==='native'&&(S.native?.published_world||S.published)||null,isDirty:false};
for(const key of ['save-check','save','saved','continue','home','held','import','world-picked','source','import-zip','zip','refresh','ai-cancel','ai-check','next','ai-propose','ai-target','ai-instruction','ai-apply','generate','new-tools','preserve-section','preserve','detail-editor','json','json-apply','prepare','recovery','recovery-note'])$(key);
vm.createContext(context);vm.runInContext(src.slice(src.indexOf('function controls(){'),src.indexOf('function carry(){')),context);
context.controls();assert.equal($('save').hidden,false);assert.equal($('next').hidden,false);assert.equal($('next').textContent,'内容を整える →');assert.equal($('recovery').hidden,true);
context.isDirty=true;context.controls();assert.match($('saved').textContent,/未保存/);assert.equal($('save').disabled,false);
S.generationGate=true;context.controls();assert.equal($('save').disabled,true);assert.equal($('next').disabled,true);S.generationGate=false;context.initialGeneration.active=()=>true;context.controls();assert.equal($('generate').disabled,true);assert.equal($('ai-propose').disabled,true);context.initialGeneration.active=()=>false;
S.step=2;S.saved='signature';context.isDirty=false;context.controls();assert.equal($('next').textContent,'確認へ進む →');assert.match($('saved').textContent,/再開位置：①/);
S.step=3;context.controls();assert.equal($('next').textContent,'この内容で世界を作成');assert.equal($('next').disabled,true);S.checked={signature:'signature'};$('consent').checked=true;context.controls();assert.equal($('next').disabled,false);
S.published='world-created';context.controls();assert.equal($('next').textContent,'保存した世界を開く →');
for(const screen of ['entry','zip-import','resume']){S.screen=screen;context.controls();assert.equal(footer.hidden,true);assert.equal($('save').hidden,true);assert.equal($('next').hidden,true);}
S.screen='world-import';context.controls();assert.equal(footer.hidden,false);assert.equal($('import').hidden,false);assert.equal($('save').hidden,true);assert.equal($('next').hidden,true);
S.screen='workflow';S.step=2;S.published=null;S.unknown=true;S.pending={action:'save',kind:'new',id:'a'.repeat(32)};context.controls();assert.equal($('recovery').hidden,false);assert.equal($('save-check').disabled,false);assert.equal($('save').disabled,true);
S.unknown=false;S.conflict=true;context.controls();assert.equal($('save-check').disabled,false);assert.match($('recovery-note').textContent,/別の画面/);assert.equal($('next').disabled,true);
S.pending=null;context.controls();assert.equal($('save-check').disabled,true);
S.conflict=false;S.kind='native';S.native={published_world:'world-existing'};S.step=3;S.checked={signature:'signature'};context.controls();assert.equal($('next').hidden,true);assert.equal($('save').hidden,true);assert.equal($('new-tools').hidden,true);
for(const hook of ['back','more','fork','export','original'])assert(!src.includes("$('"+hook+"')"));
assert(!src.includes('async function fork('));assert(!src.includes('async function exportNative('));
console.log('PASS footer controls: correct actions per route and stage, unsaved/saved, check+consent gate, publication, GET recovery visibility, conflict locking, published-native restriction retained; no hidden fork/export/back/more entry point.');

const line=n=>src.split('\n').find(l=>l.startsWith('async function '+n+'(')||l.startsWith('function '+n+'('));
const pending={action:'save',kind:'new',id:'b'.repeat(32),content:{name:'my unsaved edit'}};
const c={S:{busy:false,unknown:false,conflict:true,pending},fp:JSON.stringify,api:async()=>({content:{name:'another tab edit'}}),controls:()=>{},message:t=>c.note=t,$:()=>({textContent:''})};
vm.createContext(c);vm.runInContext(src.slice(src.indexOf('async function recover(){'),src.indexOf('async function propose(){')),c);
(async()=>{await c.recover();assert.equal(c.S.conflict,true);assert.equal(c.S.pending,null);assert.match(c.note,/比較/);console.log('PASS changed latest save: GET retains current input, exposes explicit comparison, and does not reload, overwrite, retry or clone.');})().catch(e=>{console.error(e);process.exitCode=1;});

const stageContext={S:{kind:'new',originalContent:{workflow:{version:1,step:1}}}};vm.createContext(stageContext);vm.runInContext(line('savedStage'),stageContext);assert.equal(stageContext.savedStage(),'① 下書きを用意');stageContext.S.kind='native';stageContext.S.native={buffer:{}};assert.equal(stageContext.savedStage(),'未記録');stageContext.S.native.buffer.workflow={version:1,step:3};assert.equal(stageContext.savedStage(),'③ 確認して採用');
