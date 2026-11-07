(() => {
 'use strict';
 const names=value=>Array.isArray(value)?value.map(v=>typeof v==='string'?v:v.name||v.id):Object.keys(value||{});
 const kinds={vitality:'命・生存状態',state:'制度や世界の状態',knowledge:'情報が誰に知られたか',inventory:'持ち物の必要量',zone:'人物の居場所',co_location:'二人が同じ場所にいる',relation:'一方向の親しさ'};
 function render(root,draft,snapshot,editable,change){
  root.replaceChildren();
  const people=snapshot.people.map(p=>p.id),world=snapshot.world,states=[...Object.keys(world.deliberation?.state||{}),...Object.keys(world.responses?.state||{})],facts=Array.isArray(world.facts)?world.facts.filter(f=>typeof f==='string'||f.values==null).map(f=>typeof f==='string'?f:f.id):Object.entries(world.facts||{}).filter(([,f])=>f?.values==null).map(([id])=>id),items=names(world.items),zones=names(world.zones);
  const title=document.createElement('h2');title.textContent='状態・条件の定義';root.append(title);
  if(!Array.isArray(draft.conditions)||draft.conditions.some(c=>!c||typeof c!=='object'||!c.test||!kinds[c.test.kind]||(c.test.kind==='knowledge'&&!Array.isArray(c.test.actors))||(c.test.kind==='vitality'&&!Array.isArray(c.test.allowed)))){
   const error=document.createElement('p');error.className='ui-error';error.textContent='この設定はフォームで扱えない形式です。設定ファイルから対象と条件を確認してください。';root.append(error);return;
  }
  const note=document.createElement('p');note.className='ui-hint';note.textContent='どの状態なら条件を満たすかを定義します。誰が何を重視するかは、登場人物の「大切にしていること」で設定できます。';root.append(note);
  function field(parent,label,value,options,update,{number=false,min,max,step,required=false}={}){
   const wrapper=document.createElement('label');wrapper.className='ui-field';const caption=document.createElement('span');caption.textContent=label;wrapper.append(caption);
   const input=document.createElement(options?'select':'input');input.disabled=!editable;
   if(options){for(const option of options){const node=document.createElement('option');node.value=typeof option==='string'?option:option[0];node.textContent=typeof option==='string'?option:option[1];input.append(node);}input.value=value;}
   else{input.type=number?'number':'text';input.value=value;input.maxLength=256;if(number){input.min=min;input.max=max;input.step=step||1;}}
   input.required=required;input.addEventListener(options?'change':'input',()=>{update(number?Number(input.value):input.value);change();});wrapper.append(input);parent.append(wrapper);return input;
  }
  function button(parent,label,fn){const b=document.createElement('button');b.type='button';b.className='ui-button ui-button--secondary';b.textContent=label;b.disabled=!editable;b.addEventListener('click',fn);parent.append(b);return b;}
  function redraw(){render(root,draft,snapshot,editable,change);}
  function defaultTest(kind){
   if(kind==='vitality')return {kind,actor:people[0]||'',allowed:['alive','downed','revived']};
   if(kind==='state')return {kind,key:states[0]||'',equals:''};
   if(kind==='knowledge')return {kind,fact:facts[0]||'',actors:people.slice(0,1),rule:'none'};
   if(kind==='inventory')return {kind,actor:people[0]||'',item:items[0]||'',minimum:1};
   if(kind==='zone')return {kind,actor:people[0]||'',equals:zones[0]||''};
   if(kind==='co_location')return {kind,actor:people[0]||'',target:people[1]||''};
   return {kind:'relation',observer:people[0]||'',target:people[1]||people[0]||'',minimum:0};
  }
  for(const [index,condition] of draft.conditions.entries()){
   const section=document.createElement('fieldset');section.className='ui-content';section.dataset.concernIndex=String(index);const legend=document.createElement('legend');legend.textContent=condition.label||`条件 ${index+1}`;section.append(legend);root.append(section);
   field(section,'条件の名前',condition.label,null,v=>{condition.label=v;legend.textContent=v||`条件 ${index+1}`;},{required:true});
   field(section,'望むこと',condition.mode,[['maintain','今の状態を維持したい'],['achieve','その状態を実現したい']],v=>condition.mode=v);
   field(section,'状態を確認できる範囲',condition.visibility,[['observed','本人が見聞きした範囲'],['public','誰でも確認できる公開情報']],v=>condition.visibility=v);
   const choices=Object.entries(kinds).filter(([k])=>!(k==='state'&&!states.length)&&!(k==='knowledge'&&!facts.length)&&!(k==='inventory'&&!items.length));
   field(section,'何の状態で判断するか',condition.test.kind,choices,v=>{condition.test=defaultTest(v);redraw();});
   const test=condition.test;
   for(const key of ['actor','observer','target'])if(key in test)field(section,{actor:'対象の人物',observer:'気持ちを持つ人物',target:test.kind==='co_location'?'一緒にいる相手':'気持ちが向かう相手'}[key],test[key],people,v=>test[key]=v,{required:true});
   if(test.kind==='state'){
    field(section,'確認する状態',test.key,states,v=>test.key=v,{required:true});field(section,'望む状態',test.equals,null,v=>test.equals=v,{required:true});
   }else if(test.kind==='vitality'){
    const group=document.createElement('fieldset');const cap=document.createElement('legend');cap.textContent='条件を満たす生存状態';group.append(cap);
    for(const [value,label] of [['alive','生存'],['downed','倒れている'],['revived','回復した'],['dead','死亡']]){
     const row=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.disabled=!editable;check.checked=test.allowed.includes(value);check.addEventListener('change',()=>{test.allowed=check.checked?[...test.allowed,value]:test.allowed.filter(v=>v!==value);change();});row.append(check,document.createTextNode(label));group.append(row);
    }section.append(group);
   }else if(test.kind==='knowledge'){
    field(section,'対象の情報',test.fact,facts,v=>test.fact=v,{required:true});field(section,'満たしたい条件',test.rule,[['none','指定した誰にも知られていない'],['all','指定した全員が知っている']],v=>test.rule=v);
    const group=document.createElement('fieldset');const cap=document.createElement('legend');cap.textContent='情報を知る人物として確認する相手';group.append(cap);
    for(const who of people){const row=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.disabled=!editable;check.checked=test.actors.includes(who);check.addEventListener('change',()=>{test.actors=check.checked?[...test.actors,who]:test.actors.filter(v=>v!==who);change();});row.append(check,document.createTextNode(who));group.append(row);}section.append(group);
   }else if(test.kind==='inventory'){
    field(section,'必要な物',test.item,items,v=>test.item=v,{required:true});field(section,'必要な個数',test.minimum,null,v=>test.minimum=v,{number:true,min:0,max:100000});
   }else if(test.kind==='zone')field(section,'いてほしい場所',test.equals,zones,v=>test.equals=v,{required:true});
   else if(test.kind==='relation')field(section,'必要な親しさ（-1から1）',test.minimum,null,v=>test.minimum=v,{number:true,min:-1,max:1,step:.05});
   button(section,'この条件を外す',()=>{draft.conditions=draft.conditions.filter(v=>v!==condition);change();redraw();});
  }
  button(root,'条件を追加',()=>{const reserved=new Set([...draft.conditions.map(v=>v.id),...people,...names(world.facts),...items,...zones]);let i=1;while(reserved.has(`concern_${i}`))i++;draft.conditions.push({id:`concern_${i}`,label:'',mode:'maintain',visibility:'observed',test:defaultTest('vitality')});change();redraw();const fields=root.querySelectorAll('input[type=text]');fields[fields.length-1]?.focus();});
 }
 function summary(root,draft,detail){
  root.replaceChildren();const heading=document.createElement('h2');heading.textContent='条件の一覧';root.append(heading);
  const note=document.createElement('p');note.className='ui-hint';note.textContent='選ぶと編集箇所へ移動します。案への予測と現実の達成は別に扱います。';root.append(note);
  if(!Array.isArray(draft.conditions))return;
  for(const [index,condition] of draft.conditions.entries()){
   if(!condition||typeof condition!=='object')continue;
   const button=document.createElement('button');button.type='button';button.className='ui-button ui-button--secondary';button.textContent=condition.label||`条件 ${index+1}`;
   button.addEventListener('click',()=>{const section=detail.querySelector(`[data-concern-index="${index}"]`);section?.scrollIntoView({block:'start'});section?.querySelector('input,select')?.focus();});root.append(button);
   const note=document.createElement('p');note.className='ui-hint';note.textContent=(condition.mode==='maintain'?'維持する状態':'実現する状態')+' ／ '+(condition.visibility==='public'?'公開情報':'本人が見聞きした範囲');root.append(note);
  }
 }
 window.WorldBloomConcerns={render,summary};
})();
