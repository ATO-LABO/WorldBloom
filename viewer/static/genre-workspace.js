(() => {
'use strict';
const newRoot = document.querySelector('[data-genre-new]');
const root = newRoot || document.querySelector('[data-genre-editor]');
if (!root) return;
const initial = JSON.parse(root.dataset.initial);
const $ = s => root.querySelector(s);
const make = (tag, text, cls) => {const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e;};
const clone = v => JSON.parse(JSON.stringify(v));
let busy=false, done=false, unknown=false;
const status = text => { $('[data-status]').textContent=text; };
async function api(path, body) {
 let response;
 try {response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});} catch (_) {throw {unknown:true,message:'通信結果を確認できませんでした。入力を控え、一覧や再読み込みで保存結果を確認してください。'};}
 let json; try {json=await response.json();} catch (_) {throw {unknown:true,message:'応答を確認できませんでした。再送せず、保存結果を確認してください。'};}
 if(!response.ok) throw json;
 return json;
}
function lock(value) {
 busy=value;
 root.querySelectorAll('input,textarea,select,button').forEach(e=>{if(value){e.dataset.wasDisabled=String(e.disabled);e.disabled=true;}else{e.disabled=e.dataset.wasDisabled==='true';delete e.dataset.wasDisabled;}});
 root.setAttribute('aria-busy',String(value));
}
root.addEventListener('click',e=>{if(busy&&e.target.closest('a'))e.preventDefault();});
if(newRoot){
 const form=$('form'), name=form.elements.name, desc=form.elements.description, ident=form.elements.template_id;
 let dirty=false;
 const mode=()=>form.elements.mode.value, source=()=>initial.genres.find(g=>g.id===form.elements.source.value);
 function preview(){
  const copy=mode()==='copy', g=source();
  $('[data-copy]').hidden=!copy;
  $('[data-preview-name]').textContent=name.value.trim()||'名前を入力してください';
  $('[data-preview-mode]').textContent=copy?'複製':'新規作成';
  $('[data-preview-description]').textContent=desc.value||'説明はあとから追加できます。';
  $('[data-preview-title]').textContent=copy?'引き継ぐ設定':'作成後に設定すること';
  const dl=$('[data-preview-facts]');dl.replaceChildren();
  for(const [label,file] of [['行動','action_graph.yaml'],['定石','canon.yaml'],['効果','effects.yaml'],['ルール','rules.yaml'],['候補の分類','qd.yaml']]){
   dl.append(make('dt',label),make('dd',copy?(g?.files.includes(file)?'設定をコピー':'設定なし'):file==='qd.yaml'?'共通設定':'未設定'));
  }
  $('[data-preview-note]').textContent=copy?'元のジャンルの設定は変わりません。':'特定の作品の設定は引き継ぎません。';
 }
 form.addEventListener('input',()=>{dirty=true;preview();});form.addEventListener('change',()=>{dirty=true;preview();});
 form.addEventListener('submit',async e=>{
  e.preventDefault();if(busy||unknown)return;
  if(!ident.validity.valid)$('[data-id-details]').open=true;
  if(!form.reportValidity())return;
  const payload={mode:mode(),template_id:ident.value,name:name.value,description:desc.value};
  if(payload.mode==='copy')payload.from_template_id=form.elements.source.value;
  lock(true);status('ジャンルを作成中…');
  try {const result=await api('/api/genres',payload);done=true;location.href='/genres/'+encodeURIComponent(result.template_id);}
  catch(error){status(error.message||'作成できませんでした。入力内容は残っています。');if(error.field_errors?.template_id)$('[data-id-details]').open=true;unknown=!!error.unknown;}
  finally{lock(false);if(unknown)$('button[type=submit]').disabled=true;}
 });
 addEventListener('beforeunload',e=>{if(!done&&(busy||dirty)){e.preventDefault();e.returnValue='';}});
 preview();return;
}
let snap=initial.snapshot, states=clone(snap.files), section='basic', variant=0;
let validated=false;
const sections=initial.sections;
const definitions={basic:'表示名と、このジャンルの説明を設定します。',actions:'使える行動と、そのつながりを設定します。',canon:'状況ごとの定石を設定します。',effects:'伏線と回収時の効果を設定します。',rules:'状況に応じて、人物が選ぶ行動の傾向を調整します。',qd:'候補を並べる分類軸を設定します。'};
const labels={name:'ジャンル名',description:'説明',id:'識別名',when:'適用する条件式',scope:'適用する場面',adjust:'調整する内容',verb:'行動',category:'行動カテゴリ',subtype:'行動の種類',risk:'リスク',sign:'方向',genres:'対象ジャンル',ctx:'状況',act:'定石となる行動',n:'重み',weight:'重み',phase:'段階',hostile_present:'敵対者がいる',objective:'目的の状態',vitality:'生存の状態',stance:'態度',role:'相手との関係',plant:'伏線を置く条件',payoff:'伏線の回収',condition:'条件式',effect:'効果',mode:'回収方法',categories:'行動カテゴリ',volatility_bins:'変動の区分',target:'対象',source:'要因',delta:'変化量',to_role:'相手との関係',reveals:'明らかになること',a:'人物A',b:'人物B',stance_shift_bias:'態度を変える傾向'};
const label=k=>labels[k]||(k.startsWith('category_weight.')?'行動カテゴリ '+k.split('.')[1]+' の重み':k);
const paths=()=>sections.find(s=>s[0]===section)[2];
const path=()=>paths()[Math.min(variant,paths().length-1)];
const title=()=>sections.find(s=>s[0]===section)[1]+(paths().length>1?(variant?'（敵役側）':'（主人公側）'):'');
const dirty=p=>states[p].content!==snap.files[p].content;
function invalidate(){validated=false;$('[data-validation]').textContent='未確認（編集中の内容）';}
function changed(p){states[p].content=JSON.stringify(states[p].value,null,2);invalidate();refresh();const rows=collection(p);if(Array.isArray(rows))root.querySelectorAll('.ge-entry>summary').forEach((e,i)=>{const row=rows[i];if(row&&typeof row==='object')e.textContent=row.description||row.id||row.verb||row.act?.verb||('項目 '+(i+1));});status(title()+'を編集中です。この項目の変更だけを保存します。');}
function refresh(){
 const changes=Object.keys(states).filter(dirty);
 $('[data-dirty-badge]').textContent=changes.length?'未保存の変更':'保存済み';
 $('[data-save]').disabled=busy||unknown||!dirty(path());
 $('[data-reset]').disabled=busy||!dirty(path());
 $('[data-save]').textContent=title()+'を保存';
 $('[data-changes]').replaceChildren(...(changes.length?changes.map(p=>make('li',sections.find(s=>s[2].includes(p))[1]+(p.includes('antagonist')?'（敵役側）':''))):[make('li','変更はありません')]));
 root.querySelectorAll('[data-section]').forEach(e=>{e.setAttribute('aria-current',e.dataset.section===section?'page':'false');e.querySelector('span').textContent=sections.find(s=>s[0]===e.dataset.section)[2].some(dirty)?' ●':'';});
 const meta=states['genre.json'].value;
 if(meta&&typeof meta.name==='string')$('[data-genre-name]').textContent=meta.name||snap.id;
}
function field(obj,key,parent,p){
 const value=obj[key], cap=label(String(key));
 if(value!==null&&typeof value==='object'){
  const group=make('fieldset',undefined,'ge-group');group.append(make('legend',cap));
  if(Array.isArray(value)){
   value.forEach((v,i)=>{const row=make('div',undefined,'ge-array-row');field(value,i,row,p);const remove=make('button','削除','ge-small');remove.type='button';remove.addEventListener('click',()=>{value.splice(i,1);changed(p);render();});row.append(remove);group.append(row);});
   const add=make('button','＋ 項目を追加','ge-small');add.type='button';add.addEventListener('click',()=>{value.push('');changed(p);render();});group.append(add);
  }else Object.keys(value).forEach(k=>field(value,k,group,p));
  parent.append(group);return;
 }
 const wrapper=make('label',undefined,'ge-field');wrapper.append(make('span',/^\d+$/.test(cap)?'項目 '+(Number(cap)+1):cap));
 let input;
 if(typeof value==='boolean'){input=make('input');input.type='checkbox';input.checked=value;}
 else if(key==='scope'){input=make('select');['candidate','turn',...(!['candidate','turn'].includes(value)?[value]:[])].forEach(v=>{const o=make('option',v==='candidate'?'行動候補を選ぶとき':v==='turn'?'ターンごと':String(v));o.value=v;input.append(o);});input.value=value;}
 else{input=make(key==='description'||key==='when'||key==='condition'?'textarea':'input');if(typeof value==='number'){input.type='number';input.step='any';}input.value=value===null?'':String(value);if(key==='name'){input.required=true;input.maxLength=120;}}
 input.addEventListener('input',()=>{obj[key]=typeof value==='boolean'?input.checked:typeof value==='number'?(input.value===''?null:input.valueAsNumber):value===null&&input.value===''?null:input.value;changed(p);});
 wrapper.append(input);parent.append(wrapper);
}
function collection(p){const v=states[p].value;if(p.startsWith('action_graph'))return v?.nodes;if(p.startsWith('canon'))return v?.entries;if(p==='rules.yaml'||p==='effects.yaml')return v;return null;}
function seed(p){
 if(p.startsWith('action_graph'))return {verb:'observe',category:'II',risk:'neutral',sign:0};
 if(p.startsWith('canon'))return {ctx:{},act:{category:'II',verb:'observe',role:'neutral'},n:1};
 if(p==='rules.yaml')return {id:'rule_'+Date.now(),description:'新しいルール',scope:'candidate',when:'True',adjust:{'category_weight.I':0}};
 return {id:'effect_'+Date.now(),plant:{verb:'observe'},payoff:{condition:'True',description:'',effect:{},mode:'chosen'}};
}
function rawEditor(container,p){
 const d=make('details',undefined,'ge-advanced');d.append(make('summary','条件式・詳細な設定を編集'));
 d.append(make('p','この項目の設定全体を編集します。追加の設定も保持されます。','ge-hint'));
 const raw=make('textarea',undefined,'ge-raw');raw.setAttribute('aria-label','詳細な設定');raw.spellcheck=false;raw.value=states[p].content??JSON.stringify(states[p].value,null,2);
 d.addEventListener('toggle',()=>{if(d.open)raw.value=states[p].content??JSON.stringify(states[p].value,null,2);});
 raw.addEventListener('input',()=>{states[p].content=raw.value;states[p].pending=true;status(title()+'の詳細を編集中です。保存はまだ行っていません。');$('[data-add]').disabled=true;invalidate();refresh();$('[data-save]').disabled=false;container.querySelectorAll('[data-structured] input,[data-structured] textarea,[data-structured] select,[data-structured] button').forEach(e=>e.disabled=true);});
 const apply=make('button','詳細の変更をフォームへ反映');apply.type='button';
 apply.addEventListener('click',async()=>{if(busy)return;lock(true);try{const result=await api('/api/genres/'+encodeURIComponent(snap.id)+'/parse',{path:p,content:states[p].content??raw.value});states[p].value=result.value;states[p].form=result.form;states[p].pending=false;states[p].error=null;render();status(result.form?'フォームに反映しました。保存はまだ行っていません。':'この設定は詳細編集で扱います。');}catch(error){status(error.message||'書式を確認してください');}finally{lock(false);refresh();}});
 d.append(raw,apply);container.append(d);
}
function render(){
 const p=path(),state=states[p], container=$('[data-editor]');container.replaceChildren();
 $('[data-title]').textContent=title();$('[data-help]').textContent=definitions[section];
 $('[data-role]').hidden=paths().length<2;
 root.querySelectorAll('[data-variant]').forEach(e=>e.setAttribute('aria-pressed',String(Number(e.dataset.variant)===variant)));
 const rows=collection(p);$('[data-add]').hidden=!Array.isArray(rows)||state.pending;$('[data-add]').disabled=!!state.pending;
 if((state.content===null||(!state.pending&&state.value===null&&state.form))&&p!=='genre.json'){
  container.append(make('p','この項目は未設定です。共通設定が使われます。','ge-note'));
  const create=make('button','この項目を設定する');create.type='button';create.addEventListener('click',()=>{state.value=clone(initial.defaults[p.replace('.antagonist','')]||{});state.form=true;changed(p);render();});container.append(create);refresh();return;
 }
 if(state.form&&!state.pending){
  const structured=make('div');structured.dataset.structured='true';
  if(Array.isArray(rows)){
   if(!rows.length)structured.append(make('p','まだ登録されていません。追加ボタンから設定できます。','ge-note'));
   rows.forEach((row,i)=>{const item=make('details',undefined,'ge-entry');item.open=i===0;
    item.append(make('summary',(row&&typeof row==='object'?(row.description||row.id||row.verb||row.act?.verb):null)||('項目 '+(i+1))));
    const body=make('div',undefined,'ge-entry-body');
    if(p==='rules.yaml'&&row&&typeof row==='object'){
     body.classList.add('ge-rule-body');
     if('description' in row)field(row,'description',body,p);
     const condition=make('fieldset',undefined,'ge-group ge-condition');condition.append(make('legend','適用する条件'));
     if('when' in row)field(row,'when',condition,p);body.append(condition);
     const adjustment=make('div',undefined,'ge-adjust');if('adjust' in row)field(row,'adjust',adjustment,p);body.append(adjustment);
     const more=make('details',undefined,'ge-rule-more');more.append(make('summary','識別名・適用する場面など'));
     Object.keys(row).filter(k=>!['description','when','adjust'].includes(k)).forEach(k=>field(row,k,more,p));body.append(more);
    }else if(row&&typeof row==='object')Object.keys(row).forEach(k=>field(row,k,body,p));else field(rows,i,body,p);
    const remove=make('button','この項目を削除','ge-small');remove.type='button';remove.addEventListener('click',()=>{rows.splice(i,1);changed(p);render();});(body.querySelector('.ge-rule-more')||body).append(remove);item.append(body);structured.append(item);
   });
   // Other top-level fields remain editable without flattening their structure.
   if(!Array.isArray(state.value)){const extras=Object.keys(state.value).filter(k=>k!==(p.startsWith('canon')?'entries':'nodes'));if(extras.length){const d=make('details',undefined,'ge-advanced');d.append(make('summary','共通の重み・つながりなど'));extras.forEach(k=>field(state.value,k,d,p));structured.append(d);}}
  }else if(state.value&&typeof state.value==='object'){const keys=p==='genre.json'?['name','description',...Object.keys(state.value).filter(k=>!['name','description'].includes(k))]:Object.keys(state.value);keys.forEach(k=>field(state.value,k,structured,p));}
  else structured.append(make('p','詳細編集から設定を追加できます。'));
  container.append(structured);
 }else container.append(make('p',state.pending?'詳細に未反映の変更があります。フォームに反映するか、そのまま保存できます。':state.error||'この設定は詳細編集で扱います。','ge-note'));
 if(p!=='genre.json'||!state.form)rawEditor(container,p);
 refresh();
}
root.querySelectorAll('[data-section]').forEach(e=>e.addEventListener('click',()=>{if(busy)return;section=e.dataset.section;variant=0;render();$('[data-title]').tabIndex=-1;$('[data-title]').focus();}));
root.querySelectorAll('[data-variant]').forEach(e=>e.addEventListener('click',()=>{if(busy)return;variant=Number(e.dataset.variant);render();}));
$('[data-add]').addEventListener('click',()=>{const p=path();collection(p).push(seed(p));changed(p);render();const items=$('[data-editor]').querySelectorAll('.ge-entry');items.forEach(e=>e.open=false);if(items.length){items[items.length-1].open=true;items[items.length-1].scrollIntoView({block:'nearest'});}});
$('[data-reset]').addEventListener('click',()=>{const p=path();if(states[p].remote)snap.files[p]=clone(states[p].remote);states[p]=clone(snap.files[p]);invalidate();render();status(title()+'の変更を戻しました。');});
$('[data-save]').addEventListener('click',async()=>{
 if(busy||unknown)return;const p=path();for(const el of $('[data-editor]').querySelectorAll('input,textarea,select'))if(!el.reportValidity())return;
 lock(true);status(title()+'を保存中…');
 try{
  const next=await api('/api/genres/'+encodeURIComponent(snap.id)+'/save',{path:p,content:states[p].content,revision:snap.files[p].revision});
  for(const key of Object.keys(states)){
   if(key===p||!dirty(key)){states[key]=clone(next.files[key]);snap.files[key]=clone(next.files[key]);}
   else if(next.files[key].revision!==snap.files[key].revision){states[key].remote=clone(next.files[key]);}
  }
  // A dirty item retains its old base revision, so another writer cannot be overwritten.
  snap.revision=next.revision;invalidate();render();status(title()+'を保存しました。'+(Object.keys(states).some(dirty)?'ほかの項目の変更は未保存です。':''));
 }catch(error){unknown=!!error.unknown;status(error.message||'保存できませんでした。入力は残っています。');}
 finally{lock(false);refresh();}
});
$('[data-world]').addEventListener('change',invalidate);
$('[data-validate]').addEventListener('click',async()=>{
 if(busy)return;if(Object.values(states).some(s=>s.remote)){$('[data-validation]').textContent='別の画面で変更された項目があります。変更を戻すか、入力を控えて再読み込みしてください。';return;}const world=$('[data-world]').value;if(!world){$('[data-world]').focus();$('[data-validation]').textContent='確認する世界を選んでください。';return;}
 const files=Object.fromEntries(Object.keys(states).filter(dirty).map(p=>[p,states[p].content]));
 lock(true);$('[data-validation]').textContent='確認中…';
 try{const result=await api('/api/genres/'+encodeURIComponent(snap.id)+'/check',{world_id:world,files,revision:snap.revision});validated=true;$('[data-validation]').textContent=result.world_name+'との組み合わせを確認しました。'+result.subjects+'人・結末 '+result.target_endings.length+'件'+(Object.keys(result.fallbacks).length?'（一部に共通設定を使用）':'');}
 catch(error){validated=false;$('[data-validation]').textContent=error.message||'確認できませんでした。';}
 finally{lock(false);refresh();}
});
addEventListener('beforeunload',e=>{if(busy||Object.keys(states).some(dirty)){e.preventDefault();e.returnValue='';}});
render();
})();
