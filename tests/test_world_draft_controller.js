// Controller tests with fake DOM/fetch; no browser, model, or external network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../viewer/static/world-draft.js'),'utf8');
class Element {
  constructor(tag='div'){this.tagName=tag;this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.dataset={};this.children=[];this.handlers={};this.textContent='';this.attributes={};this.isConnected=true;}
  addEventListener(type,fn){this.handlers[type]=fn;}
  emit(type){return this.handlers[type]?.({preventDefault(){}});}
  append(...nodes){this.children.push(...nodes);nodes.forEach(n=>n.parentNode=this);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(k,v){this.attributes[k]=v;}
  getAttribute(k){return this.attributes[k];}
  showModal(){this.open=true;}
  close(){this.open=false;this.emit('close');}
  remove(){this.parentNode.children=this.parentNode.children.filter(n=>n!==this);}
  focus(){}
  reportValidity(){return true;}
  set innerHTML(_){throw Error('Untrusted content must use textContent');}
}
function harness(mode='mock', saved=null, query='', withEditor=false){
  const selectors=['input','status','errors','json','confirm','adopt','validate','cancel','retry','check','review','summary','result','goals','confirm-row'];
  const nodes=Object.fromEntries(selectors.map(k=>[k,new Element()]));
  const name=new Element(),overview=new Element(),submit=new Element();name.value='人が指定した世界';overview.value='人が指定した概要';
  nodes.input.elements={name,overview};nodes.input.querySelector=()=>submit;
  nodes.input.reportValidity=()=>!!name.value.trim()&&!!overview.value.trim();
  const root=new Element();root.dataset.mode=mode;
  root.querySelector=s=>nodes[s.match(/data-wd-([^\]]+)/)?.[1]];
  const storage=new Map(saved?[['wb-new-world-draft',saved]]:[]),requests=[];
  let counter=0;
  const context={document:{querySelector:()=>root,createElement:tag=>new Element(tag)},
    window:{addEventListener(){},location:{href:'http://localhost/worlds/new'+query,search:query},history:{state:null,replaceState(){}}},URL,URLSearchParams,crypto:{randomUUID:()=>String(++counter).padStart(32,'0')},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject})),console};
  if(withEditor)context.window.WorldBloomDraftGoals=require('../viewer/static/draft-goal-editor.js');
  vm.runInNewContext(source,context);
  return {nodes,name,overview,submit,requests,storage,respond(i,data,status=200){requests[i].resolve({ok:status>=200&&status<300,status,json:async()=>data});}};
}
function proposal(){return {state:'review',revision:'revision-1',name:'指定名',overview:'指定概要',errors:{},draft:{name:'<script>bad</script>',protagonist:'A',antagonist:'B',initial_story:'<img src=x>',people:[{name:'A',description:'<b>text</b>',personality:'穏やか',entry:'町',goal:{target:'種',deliver_to:'町'},relations:[]}],places:[{name:'町',description:'町の説明'}],items:[{name:'種',holder:'B'}],routes:[]}};}
async function generated(h){const p=h.nodes.input.emit('submit');h.respond(0,proposal());await p;return h;}
async function test(name,fn){await fn();console.log('PASS '+name);}
(async()=>{
  await test('default waiting never sends a generation request',async()=>{const h=harness('waiting');await h.nodes.input.emit('submit');assert.equal(h.requests.length,0);assert.equal(h.submit.disabled,true);});
  await test('double generation and adoption each send once; review and confirmation required',async()=>{
    const h=harness();const p=h.nodes.input.emit('submit');await h.nodes.input.emit('submit');assert.equal(h.requests.length,1);assert.equal(h.nodes.adopt.disabled,true);
    h.respond(0,proposal());await p;assert.equal(h.nodes.review.hidden,false);assert.equal(h.nodes.adopt.disabled,true);
    h.nodes.confirm.checked=true;h.nodes.confirm.emit('change');assert.equal(h.nodes.adopt.disabled,false);
    const a=h.nodes.adopt.emit('click');await h.nodes.adopt.emit('click');assert.equal(h.requests.length,2);
    h.respond(1,{state:'adopted',world_id:'world-new',errors:{}});await a;assert.equal(h.nodes.result.children[0].href,'/worlds/world-new');assert.equal(h.storage.size,0);
  });
  await test('cancel invalidates late generation; retry needs a fresh explicit click',async()=>{
    const h=harness(),p=h.nodes.input.emit('submit'),c=h.nodes.cancel.emit('click');assert.equal(h.requests.length,2);
    h.respond(1,{state:'cancelled',errors:{}});await c;h.respond(0,proposal());await p;assert.equal(h.nodes.review.hidden,true);
    await h.nodes.retry.emit('click');assert.equal(h.requests.length,2);assert.equal(h.submit.disabled,false);assert.equal(h.name.disabled,false);
  });
  await test('unknown result uses status read instead of automatic regeneration',async()=>{
    const h=harness(),p=h.nodes.input.emit('submit');h.requests[0].reject(new Error('connection lost'));await p;
    assert.equal(h.nodes.check.hidden,false);assert.equal(h.nodes.retry.hidden,true);assert.equal(h.requests.length,1);
    const c=h.nodes.check.emit('click');assert.equal(h.requests[1].options.method,undefined);h.respond(1,proposal());await c;assert.equal(h.nodes.review.hidden,false);assert.equal(h.nodes.confirm.checked,false);
  });
  await test('edited or malformed JSON cannot be adopted; error preserves input',async()=>{
    const h=await generated(harness());h.nodes.confirm.checked=true;h.nodes.confirm.emit('change');h.nodes.json.value='{';h.nodes.json.emit('input');
    assert.equal(h.nodes.adopt.disabled,true);await h.nodes.validate.emit('click');assert.equal(h.requests.length,1);assert.equal(h.nodes.json.value,'{');
    h.nodes.json.value=JSON.stringify(proposal().draft);const v=h.nodes.validate.emit('click');h.respond(1,{message:'参照不正',field_errors:{'people.0.entry':'場所がありません'}},400);await v;
    assert.match(h.nodes.errors.textContent,/people.0.entry/);assert.equal(h.nodes.adopt.disabled,true);
  });
  await test('save failure is uncertain and requires reread; no double save',async()=>{
    const h=await generated(harness());h.nodes.confirm.checked=true;h.nodes.confirm.emit('change');const a=h.nodes.adopt.emit('click');h.respond(1,{message:'storage error'},500);await a;
    assert.equal(h.nodes.check.hidden,false);assert.equal(h.nodes.adopt.disabled,true);assert.equal(h.nodes.retry.hidden,true);assert.equal(h.requests.length,2);
  });
  await test('reload resumes stored draft ID with a status read',async()=>{
    const h=harness('mock','a'.repeat(32));assert.equal(h.submit.disabled,true);assert.equal(h.nodes.check.hidden,false);
    const c=h.nodes.check.emit('click');assert.equal(h.requests[0].url,'/api/world-drafts/'+'a'.repeat(32));h.respond(0,proposal());await c;assert.equal(h.name.value,'指定名');assert.equal(h.nodes.confirm.checked,false);
  });
  await test('untrusted draft text remains text; no innerHTML interpolation',async()=>{const h=await generated(harness());assert.match(h.nodes.summary.children[0].textContent,/<script>/);assert.equal(h.nodes.summary.children.length,5);});
  await test('non-delivery goals show intent and exact completion conditions',async()=>{
    const h=harness(),p=h.nodes.input.emit('submit'),r=proposal();
    r.draft.people[0].goal={description:'<b>街の秘密を解明する</b>',conditions:[{type:'discover',fact:'地下の水車'}]};
    r.draft.items=[];r.draft.facts=[{name:'地下の水車',description:'鐘を動かす機構',place:'町'}];
    h.respond(0,r);await p;
    const text=n=>[n.textContent,...n.children.map(text)].join(' '),summary=text(h.nodes.summary);
    assert.match(summary,/<b>街の秘密を解明する<\/b>/);assert.match(summary,/地下の水車を知る/);
    assert.match(summary,/鐘を動かす機構/);assert.doesNotMatch(summary,/へ届ける/);
  });
  await test('narrative-only goals do not claim automatic completion',async()=>{
    const h=harness(),p=h.nodes.input.emit('submit'),r=proposal();
    r.draft.people[0].goal={description:'自分の生き方を見つける',conditions:[]};h.respond(0,r);await p;
    const text=n=>[n.textContent,...n.children.map(text)].join(' ');
    assert.match(text(h.nodes.summary),/自動判定なし/);
  });
  await test('invalid retained draft is editable but requires an explicit recheck',async()=>{
    const h=harness('live'),p=h.nodes.input.emit('submit'),r=proposal();r.state='invalid';r.edit_revision='repair-1';r.errors={'people.0.goal.conditions':'meet不足'};
    h.respond(0,r);await p;
    assert.equal(h.nodes.review.hidden,false);assert.equal(h.nodes.validate.hidden,false);assert.equal(h.nodes.adopt.hidden,true);assert.equal(h.nodes['confirm-row'].hidden,true);
    assert.equal(h.requests.length,1);const v=h.nodes.validate.emit('click');
    assert.equal(JSON.parse(h.requests[1].options.body).revision,'repair-1');
    h.respond(1,proposal());await v;assert.equal(h.nodes.validate.hidden,true);assert.equal(h.nodes.confirm.checked,false);
    assert.equal(h.nodes.adopt.disabled,true);
  });
  await test('unknown retained data and obsolete receipt cannot bypass revalidation',async()=>{
    const h=harness(),p=h.nodes.input.emit('submit'),r=proposal();r.state='unknown';r.edit_revision='repair';h.respond(0,r);await p;
    assert.equal(h.nodes.validate.hidden,false);assert.equal(h.nodes.adopt.hidden,true);
    const c=h.nodes.check.emit('click');r.state='review';r.needs_revalidation=true;h.respond(1,r);await c;
    h.nodes.confirm.checked=true;h.nodes.confirm.emit('change');await h.nodes.adopt.emit('click');
    assert.equal(h.requests.length,2);assert.equal(h.nodes.validate.hidden,false);
  });
  await test('draft URL resumes with a read only request and rejects malformed ID',async()=>{
    const id='b'.repeat(32),h=harness('live',null,'?draft='+id);
    assert.equal(h.requests.length,0);const p=h.nodes.check.emit('click');
    assert.equal(h.requests[0].url,'/api/world-drafts/'+id);assert.equal(h.requests[0].options.method,undefined);
    h.respond(0,proposal());await p;
    const bad=harness('live',null,'?draft=../other');assert.equal(bad.submit.disabled,false);assert.equal(bad.requests.length,0);
  });
  await test('cancellation supersedes a late validation response',async()=>{
    const h=await generated(harness());h.nodes.json.emit('input');
    const v=h.nodes.validate.emit('click'),c=h.nodes.cancel.emit('click');
    h.respond(2,{state:'cancelled',errors:{}});await c;
    h.respond(1,{message:'取り消されました'},409);await v;
    assert.equal(h.nodes.review.hidden,true);assert.match(h.nodes.status.textContent,/取り消しました/);
    assert.equal(h.nodes.check.hidden,true);
  });
  await test('person and place dialog edits invalidate approval and survive validation payload',async()=>{
    const h=await generated(harness('mock',null,'',true)),all=n=>[n,...n.children.flatMap(all)];
    h.nodes.confirm.checked=true;h.nodes.confirm.emit('change');assert.equal(h.nodes.adopt.disabled,false);
    const edit=label=>all(h.nodes.goals).find(n=>n.attributes['aria-label']===label).emit('click');
    const save=(value)=>{const dialog=all(h.nodes.goals).find(n=>n.tagName==='dialog');all(dialog).find(n=>n.tagName==='textarea').value=value;dialog.children[0].emit('submit');};
    edit('Aの人物紹介を編集');save('新しい人物紹介');assert.equal(h.requests.length,1);assert.equal(h.nodes.adopt.hidden,true);assert.equal(h.nodes.confirm.checked,false);
    edit('町の場所の説明を編集');save('新しい場所の説明');
    const edited=JSON.parse(h.nodes.json.value);assert.equal(edited.people[0].description,'新しい人物紹介');assert.equal(edited.places[0].description,'新しい場所の説明');
    const v=h.nodes.validate.emit('click'),sent=JSON.parse(h.requests[1].options.body);assert.equal(sent.draft.people[0].description,'新しい人物紹介');assert.equal(sent.draft.places[0].description,'新しい場所の説明');
    const result=proposal();result.draft=sent.draft;result.revision='edited';h.respond(1,result);await v;
    assert.equal(h.nodes.confirm.checked,false);assert.equal(h.nodes.adopt.disabled,true);assert.equal(JSON.parse(h.nodes.json.value).places[0].description,'新しい場所の説明');
  });
  await test('story and directional relation edits are local until explicit validation',async()=>{
    const h=harness('mock',null,'',true),all=n=>[n,...n.children.flatMap(all)],p=h.nodes.input.emit('submit'),initial=proposal();
    initial.draft.people.push({name:'B',description:'相手の紹介',personality:'穏やか',entry:'町',goal:{description:'町で暮らす',conditions:[]},relations:[]});h.respond(0,initial);await p;
    const open=()=>all(h.nodes.goals).find(n=>n.tagName==='dialog'),fields=()=>Object.fromEntries(all(open()).filter(n=>n.name).map(n=>[n.name,n]));
    all(h.nodes.goals).find(n=>n.textContent==='✎ 導入文を編集').emit('click');fields().initial_story.value='修正した初期物語';open().children[0].emit('submit');
    all(h.nodes.goals).find(n=>n.textContent==='＋ 関係を追加').emit('click');assert.deepEqual(fields().pair.children.map(n=>n.value),['','0:1','1:0']);
    fields().pair.value='0:1';fields().label.value='幼なじみ';fields().affinity.value='0.25';fields().awareness.value='0.9';open().children[0].emit('submit');
    assert.equal(h.requests.length,1);assert.equal(h.nodes.adopt.hidden,true);
    const v=h.nodes.validate.emit('click'),sent=JSON.parse(h.requests[1].options.body);assert.equal(sent.draft.initial_story,'修正した初期物語');assert.deepEqual(sent.draft.people[0].relations,[{person:'B',label:'幼なじみ',affinity:0.25,awareness:0.9}]);assert.deepEqual(sent.draft.people[1].relations,[]);
    const r=proposal();r.draft=sent.draft;h.respond(1,r);await v;assert.equal(JSON.parse(h.nodes.json.value).initial_story,'修正した初期物語');
  });
  await test('item and route edits clear approval and validate exact local values',async()=>{
    const h=harness('mock',null,'',true),all=n=>[n,...n.children.flatMap(all)],p=h.nodes.input.emit('submit'),initial=proposal();
    initial.draft.people.push({name:'B',entry:'町',goal:{description:'暮らす',conditions:[]},relations:[]});initial.draft.places.push({name:'森',description:'森'});initial.draft.routes=[{from:'町',to:'森'}];h.respond(0,initial);await p;
    h.nodes.confirm.checked=true;h.nodes.confirm.emit('change');assert.equal(h.nodes.adopt.disabled,false);
    const open=()=>all(h.nodes.goals).find(n=>n.tagName==='dialog'),fields=()=>Object.fromEntries(all(open()).filter(n=>n.name).map(n=>[n.name,n]));
    all(h.nodes.goals).find(n=>n.attributes['aria-label']==='種を編集').emit('click');fields().name.value='鍵';fields().holder.value='B';open().children[0].emit('submit');
    assert.equal(h.nodes.confirm.checked,false);assert.equal(h.nodes.adopt.hidden,true);assert.equal(h.requests.length,1);
    all(h.nodes.goals).find(n=>n.attributes['aria-label']==='町 → 森 の経路を編集').emit('click');fields().cost.value='2.5';fields().requires_item.value='鍵';open().children[0].emit('submit');
    assert.equal(h.requests.length,1);const v=h.nodes.validate.emit('click'),sent=JSON.parse(h.requests[1].options.body);
    assert.deepEqual(sent.draft.items,[{name:'鍵',holder:'B'}]);assert.equal(sent.draft.people[0].goal.target,'鍵');assert.deepEqual(sent.draft.routes,[{from:'町',to:'森',cost:2.5,requires_item:'鍵'}]);
    const r=proposal();r.draft=sent.draft;r.revision='new-items';h.respond(1,r);await v;assert.equal(h.nodes.confirm.checked,false);assert.equal(h.nodes.adopt.disabled,true);
  });
})().catch(error=>{console.error(error);process.exitCode=1;});
