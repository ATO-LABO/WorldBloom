(() => {
 'use strict';
 const effective=(person,world)=>person.commitments??world.concerns?.commitments?.[person.id]??[];
 function render(root,draft,world,change){
  root.replaceChildren();
  const note=document.createElement('p');note.className='ui-hint';note.textContent='この人物が重視する条件を選び、優先順位と譲れなさを設定します。優先順位は大きい数を先に比べます。';root.append(note);
  const conditions=world.concerns?.conditions||[];
  const missing=draft.filter(v=>!conditions.some(c=>c.id===v.condition));
  const all=[...conditions,...missing.map(v=>({id:v.condition,label:`未登録の条件：${v.condition}`}))];
  if(!all.length){const p=document.createElement('p');p.textContent='世界の「状態・条件の定義」で条件を追加すると、ここで選べます。';root.append(p);}
  for(const condition of all){
   let value=draft.find(v=>v.condition===condition.id);
   const group=document.createElement('fieldset');group.className='ui-content';
   const legend=document.createElement('legend');legend.textContent=condition.label;group.append(legend);
   const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=!!value;label.append(check,document.createTextNode('この条件を大切にする'));group.append(label);
   const controls=document.createElement('div');controls.hidden=!value;
   function field(caption,input){const label=document.createElement('label');label.className='ui-field';const span=document.createElement('span');span.textContent=caption;label.append(span,input);controls.append(label);}
   const priority=document.createElement('input');priority.type='number';priority.min='1';priority.max='10';priority.step='1';priority.value=value?.priority??2;priority.required=!!value;priority.disabled=!value;
   priority.addEventListener('input',()=>{value.priority=Number(priority.value);change();});field('優先順位（1から10、大きい数を優先）',priority);
   const required=document.createElement('select');for(const [key,text] of [['false','他の価値と比較して判断する'],['true','譲れない条件にする']]){const option=document.createElement('option');option.value=key;option.textContent=text;required.append(option);}required.value=String(value?.required??false);required.disabled=!value;
   required.addEventListener('change',()=>{value.required=required.value==='true';change();});field('条件を満たさない案への態度',required);
   check.addEventListener('change',()=>{
    if(check.checked){value={condition:condition.id,priority:Number(priority.value),required:required.value==='true'};draft.push(value);}
    else{draft.splice(draft.indexOf(value),1);value=null;}
    controls.hidden=!value;priority.required=!!value;priority.disabled=!value;required.disabled=!value;change();
   });
   group.append(controls);root.append(group);
  }
 }
 window.WorldBloomPersonValues={effective,render};
})();
