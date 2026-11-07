(() => {
 'use strict';
 const empty=()=>({version:1,definitions:[]});
 function convert(expr,next,fallback){const previous=Object.keys(expr)[0],value=JSON.parse(JSON.stringify(expr));if(next===previous)return value;if(['all','any'].includes(next))return {[next]:['all','any'].includes(previous)?value[previous]:[value]};if(next==='not')return {not:value};if(next==='constant')return {constant:false};return {condition:previous==='not'&&value.not?.condition?value.not.condition:fallback};}
 function render(root,draft,snapshot,editable,change,view={open:new Set()}){
  root.replaceChildren();
  const conditions=snapshot.world.concerns?.conditions||[],total=(snapshot.world.time?.days||1)*(snapshot.world.time?.slots?.length||1);
  const node=(tag,text,parent)=>{const n=document.createElement(tag);if(text!==null)n.textContent=text;parent?.append(n);return n;};
  node('h2','達成・失敗・判定期間',root);
  node('p','世界で定義した条件を組み合わせて判定します。誰が重視するかは人物設定で選びます。未達成のまま期間が終わると失敗になり、確定した結果は後から状態が変わっても残ります。',root).className='ui-hint';
  if(!draft||draft.version!==1||!Array.isArray(draft.definitions)){node('p','この形式はフォームで扱えません。設定ファイルを確認してください。',root).className='ui-error';return;}
  let fieldIndex=0;
  const redraw=()=>{const focus=document.activeElement?.dataset.objectiveControl,scroll=root.scrollTop;view.open=new Set(Array.from(root.querySelectorAll('details[open][data-objective-fold]'),e=>e.dataset.objectiveFold));render(root,draft,snapshot,editable,change,view);if(focus)root.querySelector(`[data-objective-control="${focus}"]`)?.focus({preventScroll:true});root.scrollTop=scroll;};
  function button(parent,text,fn,disabled=false){const b=node('button',text,parent);b.type='button';b.className='ui-button ui-button--secondary';b.disabled=!editable||disabled;b.addEventListener('click',fn);return b;}
  function field(parent,text,value,put,options=null,number=false){
   const label=node('label',null,parent);label.className='ui-field';node('span',text,label);const input=node(options?'select':'input',null,label);input.disabled=!editable;
   input.dataset.objectiveControl=String(++fieldIndex);
   if(options){const known=new Set(options.map(([v])=>String(v)));if(!known.has(String(value))){const o=node('option','未登録：'+String(value),input);o.value=String(value);}for(const [v,title] of options){const o=node('option',title,input);o.value=String(v);}input.value=String(value);}
   else{input.type=number?'number':'text';input.value=String(value??'');if(number){input.min='1';input.max=String(total);input.step='1';input.required=true;}}
   input.addEventListener(options?'change':'input',()=>{input.focus({preventScroll:true});put(number?Number(input.value):input.value);change();});return input;
  }
  const refs=conditions.map(c=>[c.id,c.label]);
  function readable(expr,depth=0){if(depth>4||!expr||typeof expr!=='object')return '形式を確認してください';const kind=Object.keys(expr)[0],value=expr[kind];if(kind==='condition')return conditions.find(c=>c.id===value)?.label||String(value);if(kind==='constant')return value?'常に成立':'常に不成立';if(kind==='not')return '「'+readable(value,depth+1)+'」を満たさない';if(['all','any'].includes(kind)&&Array.isArray(value))return '（'+value.map(v=>readable(v,depth+1)).join(kind==='all'?'、かつ、':'、または、')+'）';return '形式を確認してください';}
  function fold(parent,title,expr,put,key){const box=node('details',null,parent);box.dataset.objectiveFold=key;box.open=view.open.has(key);node('summary',title+'：'+readable(expr),box);expression(box,expr,put,title);}
  function expression(parent,expr,put,title,depth=0){
   const group=node('fieldset',null,parent);node('legend',title,group);
   if(depth>4||!expr||typeof expr!=='object'||Array.isArray(expr)||Object.keys(expr).length!==1){node('p','条件式の形式を確認してください。保存済みの内容は変更していません。',group).className='ui-error';return;}
   const kind=Object.keys(expr)[0],value=expr[kind];
   field(group,'組み合わせ方',kind,next=>{put(convert(expr,next,refs[0]?.[0]||''));redraw();},[['condition','一つの条件'],['all','すべてを満たす'],['any','どれかを満たす'],['not','条件を満たさない'],['constant','常に成立・不成立']].filter(([key])=>depth<4||['condition','constant'].includes(key)||key===kind));
   if(kind==='condition')field(group,'参照する条件',value,v=>expr.condition=v,refs);
   else if(kind==='constant')field(group,'判定の値',String(value),v=>expr.constant=v==='true',[['false','常に不成立'],['true','常に成立']]);
   else if(kind==='not')expression(group,value,v=>expr.not=v,'反転する条件',depth+1);
   else if(['all','any'].includes(kind)&&Array.isArray(value)){
    value.forEach((child,i)=>{const part=node('div',null,group);expression(part,child,v=>value[i]=v,'組み合わせる条件 '+(i+1),depth+1);button(part,'この条件式を外す',()=>{value.splice(i,1);change();redraw();},value.length<=1);});
    button(group,'条件式を追加',()=>{value.push({condition:refs[0]?.[0]||''});change();redraw();},value.length>=16||depth>=4);
   }else node('p','条件式の形式を確認してください。',group).className='ui-error';
  }
  draft.definitions.forEach((spec,i)=>{
   const group=node('fieldset',null,root);group.className='ui-content';group.dataset.objectiveIndex=String(i);
   const condition=conditions.find(c=>c.id===spec.condition);node('legend',condition?.label||spec.condition||'判定 '+(i+1),group);
   field(group,'重視する条件との対応',spec.condition,v=>{const old=spec.condition;spec.condition=v;if(spec.success?.condition===old)spec.success.condition=v;redraw();},refs);
   if(condition?.mode!=='maintain')field(group,'達成を確定する時点',spec.completion||'first_success',v=>{if(v==='first_success')delete spec.completion;else spec.completion=v;redraw();},[['first_success','初めて達成したとき'],['period_end','期間の終了時点']]);
   node('p',condition?.mode==='maintain'?'維持する条件です。期間中に達成条件が崩れると失敗し、終了まで守り続けると達成します。':spec.completion==='period_end'?'終了時点の状態で達成を判定します。途中で条件が崩れても立て直せます。失敗確定条件が成立した場合は、その時点で失敗します。':'実現する条件です。期間中に達成すれば結果を確定します。同時に失敗条件も成立した場合は失敗を優先します。',group).className='ui-hint';
   fold(group,'達成条件',spec.success,v=>spec.success=v,i+'-success');fold(group,'失敗確定条件',spec.failure,v=>spec.failure=v,i+'-failure');
   if(!spec.period||typeof spec.period!=='object'){node('p','判定期間の形式を確認してください。',group).className='ui-error';return;}
   const period=node('fieldset',null,group);node('legend','判定期間',period);
   field(period,'開始する時点',spec.period.start,v=>spec.period.start=v,null,true);field(period,'終了する時点',spec.period.end,v=>spec.period.end=v,null,true);
   node('p','開始と終了の時点を含みます。一つの時点では各人物が一度ずつ行動でき、終了時点の全員の行動が終わってから期限を判定します。',period).className='ui-hint';
   button(group,'この判定を外す',()=>{draft.definitions.splice(i,1);change();redraw();});
  });
  const unused=conditions.filter(c=>!draft.definitions.some(s=>s.condition===c.id));
  if(!conditions.length)node('p','先に「状態・条件の定義」で、判定に使う条件を登録してください。',root);
  button(root,'判定を追加',()=>{const cid=unused[0]?.id;if(!cid)return;draft.definitions.push({condition:cid,success:{condition:cid},failure:{constant:false},period:{start:1,end:total}});change();redraw();},!unused.length);
 }
 function summary(root,draft,detail,snapshot){root.replaceChildren();const title=document.createElement('h2');title.textContent='判定の一覧';root.append(title);draft.definitions?.forEach((spec,i)=>{const b=document.createElement('button');b.type='button';b.className='ui-button ui-button--secondary';b.textContent=snapshot.world.concerns?.conditions?.find(c=>c.id===spec.condition)?.label||spec.condition;b.addEventListener('click',()=>{const group=detail.querySelector(`[data-objective-index="${i}"]`);group?.scrollIntoView({block:'start'});group?.querySelector('select')?.focus({preventScroll:true});});root.append(b);const p=document.createElement('p');p.className='ui-hint';p.textContent='判定期間 '+spec.period?.start+'〜'+spec.period?.end;root.append(p);});}
 window.WorldBloomObjectives={empty,render,summary,convert};
})();
