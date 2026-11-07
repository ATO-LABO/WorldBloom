const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const all=n=>[n,...n.children.flatMap(all)];
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.handlers={};this.attributes={};this.dataset={};this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';this.isConnected=true;}
 append(...nodes){for(const n of nodes){this.children.push(n);n.parentNode=this;}}
 replaceChildren(...nodes){for(const n of this.children)for(const c of all(n))c.isConnected=false;this.children=[];this.append(...nodes);}
 addEventListener(k,fn){this.handlers[k]=fn;}emit(k){return this.handlers[k]?.({preventDefault(){}});}click(){return this.emit('click');}
 setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}
 querySelectorAll(s){return all(this).slice(1).filter(n=>s==='input:checked'?n.tagName==='input'&&n.checked:s==='fieldset label'?n.tagName==='label'&&n.parentNode.tagName==='fieldset':s.split(',').includes(n.tagName));}
 set innerHTML(_){throw Error('Unsafe HTML');}
}
const requests=[],context={document:{createElement:t=>new Element(t)},structuredClone,crypto:{randomUUID:()=> '12345678-0000-0000-0000-000000000000'},console,fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))};
context.window={addEventListener(){},confirm:()=>true};vm.runInNewContext(fs.readFileSync(require.resolve('../viewer/static/draft-settings-forms.js'),'utf8'),context);
const api=context.window.WorldBloomDraftForms;
const model={ordinary:true,action_policy:true,time:{days:7,slots:['朝','夜'],unknown:'keep'},scheduled_events:[{id:'notice',day:1,slot:'朝',targets:['A'],future:{keep:true}}],people:[{name:'A',goal:{description:'<b>目的</b>',target:'種',deliver_to:'町',future:'keep'},commitments:[]},{name:'B',goal:{},commitments:[]}],conditions:[],places:['町','森'],items:['種'],facts:['警報'],actions:['verb:move','verb:rest'],enabled:['verb:move','verb:rest'],constraints:{},force_verbs:{move:['zone'],rest:[]}};
const root=new Element('div');let edits=[];const e=new api.Editor(root,{model,native:true,onChange:v=>edits=v});
const input=(label)=>all(root).find(n=>n.attributes['aria-label']===label),button=t=>all(root).find(n=>n.tagName==='button'&&n.textContent===t);
e.render('time');input('期間（日）').value='';input('期間（日）').emit('input');assert.equal(edits[0].values.days,'');assert.equal(model.time.days,7);assert.equal(api.effective(model,edits).time.unknown,'keep');
e.render('events');input('予定の名前').value='<img src=x>';input('予定の名前').emit('input');assert.deepEqual(Object.keys(edits.find(o=>o.kind==='event').values),['label']);assert.deepEqual(api.effective(model,edits).scheduled_events[0].future,{keep:true});
button('予定を追加').emit('click');assert.ok(edits.some(o=>o.kind==='event'&&o.target===1));assert.equal(input('実施日（第何日）').value,'');
e.render('actions');let personToggle=all(root).find(n=>n.tagName==='label'&&n.children.some(c=>c.textContent==='人物を個別に指定する')).children[0];personToggle.checked=true;personToggle.emit('change');
let checkA=all(root).find(n=>n.tagName==='label'&&n.children.some(c=>c.textContent==='A')).children[0];checkA.checked=true;checkA.emit('change');assert.deepEqual(Array.from(edits.find(o=>o.kind==='action').values.actors),['A']);
input('人物を検索').value='B';input('人物を検索').emit('input');assert.deepEqual(Array.from(edits.find(o=>o.kind==='action').values.actors),['A']);
let timeToggle=all(root).find(n=>n.tagName==='label'&&n.children.some(c=>c.textContent==='実行時間を制限する')).children[0];timeToggle.checked=true;timeToggle.emit('change');assert.ok(all(root).some(n=>n.textContent.includes('チェックした時間帯に実行できます')));
input('行動を選ぶ').value='verb:rest';input('行動を選ぶ').emit('change');assert.ok(all(root).some(n=>n.tagName==='input'&&n.disabled&&n.checked));
e.render('people');assert.equal(edits.some(o=>o.kind==='commitments'),false);input('目的の説明（自由記述）').value='手修正';input('目的の説明（自由記述）').emit('input');assert.equal(api.effective(model,edits).people[0].goal.future,'keep');assert.equal(model.people[0].goal.description,'<b>目的</b>');
button('登録済みの条件から優先事項を追加').emit('click');assert.equal(edits.find(o=>o.kind==='commitments').values.commitments[0].required,false);assert.equal(edits.find(o=>o.kind==='commitments').values.commitments[0].condition,'');
async function respond(i,data,status=200){requests[i].resolve({ok:status===200,status,json:async()=>data});for(let n=0;n<12;n++)await Promise.resolve();}
(async()=>{
 const host1=new Element('section'),host2=new Element('section');let current={name:'base'},applied=0;
 const p1=api.mount(host1,{draft:current,section:'time',getDraft:()=>current,onApply:()=>applied++});await respond(0,model);await p1;
 const p2=api.mount(host2,{draft:current,section:'events',getDraft:()=>current,onApply:()=>applied++});await respond(1,model);await p2;
 const days=all(host1).find(n=>n.attributes['aria-label']==='期間（日）');days.value='20';days.emit('input');assert.equal(api.hasPending(),true,'first widget retains guard even with two mounted forms');
 current={name:'newer human edit'};const apply=all(host1).find(n=>n.textContent==='フォームの変更を下書きに反映');await apply.emit('click');assert.equal(requests.length,2);assert.equal(applied,0);assert.ok(all(host1).some(n=>n.textContent.includes('下案が変わりました')));
 assert.equal(api.canLeave(),true);assert.equal(api.hasPending(),false);
 console.log('PASS scoped forms: incomplete values, unknown sibling retention, event edits/addition, search/selection, permitted time labels, required rest, explicit priorities, multiple pending guards and stale apply protection');
})().catch(e=>{console.error(e);process.exitCode=1});
