(() => {
 'use strict';const root=document.querySelector('[data-world-advanced]');if(!root)return;
 const initial=JSON.parse(root.dataset.initial),$=s=>root.querySelector(s),list=$('[data-advanced-list]'),detail=$('[data-advanced-detail]'),status=$('[data-advanced-status]');
 const clone=v=>JSON.parse(JSON.stringify(v)),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let world=initial.world,genre=initial.genre,files=initial.files,section='canon',index=0,variant='canon.yaml',file='world.yaml',dirty=false,busy=false,blocked=false;
 let draft=null;
 const labels={phase:'段階',hostile_present:'敵対者がいる',objective:'目的の品',vitality:'生存状態',stance:'態度',category:'カテゴリ',verb:'行動',role:'相手との関係',n:'重み'};
 const terms={hostile:'敵対',self:'自分',neutral:'中立',alive:'生存',dead:'死亡',none:'指定なし',friendly:'友好',give_item:'品物を渡す',craft:'作る',fight:'戦う',move:'移動する',rest:'休む',observe:'調べる',persuade:'説得する',cooperate:'協力する',ally:'仲間',ground:'地面',unknown:'不明',downed:'倒れている'};
 const text=v=>Array.isArray(v)?(v.length?v.join('・'):'指定なし'):v===null?'指定なし':v===true?'はい':v===false?'いいえ':terms[v]||String(v??'未設定');
 const entries=()=>Array.isArray(draft?.entries)?draft.entries:[];
 function loaded(){draft=section==='canon'?clone(genre?.files[variant]?.value??{entries:[]}):section==='roles'?{protagonist:world.world.protagonist||'',antagonist:world.world.antagonist||'',target_ending:Array.isArray(world.world.target_ending)?clone(world.world.target_ending):world.world.target_ending?[world.world.target_ending]:[]}:files[file]??'';dirty=false;}
 function refresh(){const can=initial.editable&&section!=='actions'&&(section!=='canon'||!!genre);$('[data-advanced-save]').disabled=!can||!dirty||busy||blocked;$('[data-advanced-check]').disabled=!can||busy||blocked;$('[data-advanced-reset]').disabled=!dirty||busy;root.querySelectorAll('[data-advanced-section]').forEach(b=>b.setAttribute('aria-current',b.dataset.advancedSection===section?'page':'false'));}
 function change(){dirty=true;status.textContent='未保存の変更があります';refresh();}
 function ruleText(row){return Object.entries(row?.ctx||{}).map(([k,v])=>`${labels[k]||k}：${k==='objective'?({hostile:'敵が持っている',self:'自分が持っている',ally:'仲間が持っている',ground:'地面にある'}[v]||text(v)):text(v)}`).join(' ／ ')||'条件の指定なし';}
 function ruleTitle(row){return text(row?.act?.verb)||'行動未設定';}
 function control(obj,key,parent){const wrapper=document.createElement('label'),cap=document.createElement('span');cap.textContent=labels[key]||key;wrapper.append(cap);const value=obj[key];let field;
  if(typeof value==='boolean'){field=document.createElement('select');field.innerHTML='<option value="true">はい</option><option value="false">いいえ</option>';field.value=String(value);}
  else if(typeof value==='string'&&['objective','vitality','stance','role','verb'].includes(key)){
   const options={objective:['hostile','self','ally','ground','unknown'],vitality:['alive','downed','dead'],stance:['hostile','neutral','friendly'],role:['hostile','neutral','ally','self','none'],verb:Object.keys(terms).filter(k=>!['hostile','self','neutral','alive','dead','none','friendly','ally','ground','unknown','downed'].includes(k))}[key];
   field=document.createElement('select');for(const v of new Set([value,...options])){const o=document.createElement('option');o.value=v;o.textContent=text(v);field.append(o);}field.value=value;
  }
  else{field=document.createElement(Array.isArray(value)||typeof value==='object'&&value!==null?'textarea':'input');field.value=Array.isArray(value)||typeof value==='object'&&value!==null?JSON.stringify(value):value===null?'':value;if(field.tagName==='INPUT')field.type=typeof value==='number'?'number':'text';if(field.type==='number')field.step='any';}
  field.disabled=!initial.editable;field.addEventListener('input',()=>{field.setCustomValidity('');let next=field.value;if(typeof value==='boolean')next=next==='true';else if(typeof value==='number'){next=Number(next);if(!Number.isFinite(next)){field.setCustomValidity('数値を入力してください');return;}}else if(Array.isArray(value)||typeof value==='object'&&value!==null){try{next=JSON.parse(next);if(Array.isArray(value)?!Array.isArray(next):!next||typeof next!=='object'||Array.isArray(next))throw Error();}catch{field.setCustomValidity('元と同じ形式のJSONで入力してください');return;}}else if(value===null&&next==='')next=null;obj[key]=next;change();const readback=detail.querySelector('[data-readback]');if(readback)readback.textContent=ruleText(entries()[index])+' → '+ruleTitle(entries()[index]);});wrapper.append(field);parent.append(wrapper);
 }
 function renderDetail(){detail.replaceChildren();
  if(section==='canon'){
   const row=entries()[index];detail.innerHTML='<h2>選んだ定石</h2><p class="ux-note">ジャンル「'+esc(genre?.id||'未設定')+'」の共有設定です。このジャンルを使う他の世界にも影響します。保存済みの実験は変わりません。</p>';
   if(!row){detail.insertAdjacentHTML('beforeend','<p>定石を選ぶか、追加してください。</p>');return;}
   if(!row.ctx||typeof row.ctx!=='object'||Array.isArray(row.ctx)||!row.act||typeof row.act!=='object'||Array.isArray(row.act)){detail.insertAdjacentHTML('beforeend','<p>この定石はフォームで扱えない形式です。ジャンル編集の設定ファイルで確認してください。</p>');return;}
   for(const [key,title] of [['ctx','適用する状況'],['act','選ぶ行動']]){const group=document.createElement('fieldset');group.innerHTML='<legend>'+title+'</legend>';for(const k of Object.keys(row[key]))control(row[key],k,group);if(key==='ctx'){const fold=document.createElement('details');fold.innerHTML='<summary>適用する状況を編集</summary><p>'+esc(ruleText(row))+'</p>';fold.append(group);detail.append(fold);}else detail.append(group);}if('n'in row)control(row,'n',detail);
   detail.insertAdjacentHTML('beforeend','<p class="ux-readback" data-readback>'+esc(ruleText(row)+' → '+ruleTitle(row))+'</p>');
   const canon=document.createElement('details');canon.innerHTML='<summary>定石の早見表を確認</summary>';canon.append($('[data-advanced-canon]').content.cloneNode(true));detail.append(canon);if(genre)detail.insertAdjacentHTML('beforeend','<a href="/genres/'+encodeURIComponent(genre.id)+'">ジャンル全体を編集 →</a>');
  }else if(section==='roles'){
   detail.innerHTML='<h2>役割と目標の結末</h2><p>この世界の登場人物から選びます。</p>';
   for(const [key,title] of [['protagonist','主人公'],['antagonist','敵役']]){const label=document.createElement('label');label.textContent=title;const select=document.createElement('select');select.innerHTML='<option value="">未設定</option>'+world.people.map(p=>'<option value="'+esc(p.id)+'">'+esc(p.id)+'</option>').join('');select.value=draft[key];select.disabled=!initial.editable;select.addEventListener('change',()=>{draft[key]=select.value;change();});label.append(select);detail.append(label);}
   const field=document.createElement('fieldset');field.innerHTML='<legend>目標の結末</legend>';for(const ending of world.world.ending||[]){const label=document.createElement('label');label.className='ux-inline';const check=document.createElement('input');check.type='checkbox';check.checked=draft.target_ending.includes(ending.id);check.disabled=!initial.editable;check.addEventListener('change',()=>{draft.target_ending=check.checked?[...draft.target_ending,ending.id]:draft.target_ending.filter(v=>v!==ending.id);change();});label.append(check,document.createTextNode(ending.label||ending.id));field.append(label);}detail.append(field);const readouts=document.createElement('details');readouts.innerHTML='<summary>人物の能力・秘密・伏線を確認</summary>';readouts.append($('[data-advanced-readouts]').content.cloneNode(true));detail.append(readouts);

  }else if(section==='files'){
   detail.innerHTML='<h2>'+esc(file)+'</h2><p class="ux-note">世界の設定ファイルを直接編集します。別画面で変更されていた場合は保存を止めます。</p>';const label=document.createElement('label');label.textContent='設定内容';const area=document.createElement('textarea');area.className='ux-code';area.spellcheck=false;area.value=draft;area.readOnly=!initial.editable;area.addEventListener('input',()=>{draft=area.value;change();});label.append(area);detail.append(label);
  }else{detail.innerHTML='<h2>行動の詳細</h2><p>左の行動図鑑を展開して、条件と結果を確認できます。</p>'+(genre?'<a href="/genres/'+encodeURIComponent(genre.id)+'">ジャンルの行動を編集 →</a>':'');}
 }
 function render(){list.replaceChildren();
  if(section==='canon'){
   list.innerHTML='<h2>状況ごとの定石</h2>';
   if(!genre){list.insertAdjacentHTML('beforeend','<p>ジャンルが未設定です。</p>');}
   else{const select=document.createElement('select');select.setAttribute('aria-label','定石の役割');select.innerHTML='<option value="canon.yaml">主人公側</option><option value="canon.antagonist.yaml">敵役側</option>';select.value=variant;select.addEventListener('change',()=>{if(dirty||busy){select.value=variant;status.textContent='先に変更を保存するか、変更を戻してください。';return;}variant=select.value;index=0;loaded();render();});list.append(select);
    if(genre.files[variant]?.error)list.insertAdjacentHTML('beforeend','<p>設定の書式を読み取れません。ジャンル編集で確認してください。</p>');
    entries().forEach((row,i)=>{const b=document.createElement('button');b.className='ux-rule';b.type='button';b.setAttribute('aria-pressed',String(i===index));b.innerHTML='<strong>'+esc(ruleTitle(row))+'</strong><small>'+esc(ruleText(row))+'</small>';b.addEventListener('click',()=>{index=i;render();});list.append(b);});
    if(initial.editable&&!genre.files[variant]?.error){const add=document.createElement('button');add.type='button';add.textContent='＋ 定石を追加';add.addEventListener('click',()=>{if(!draft||typeof draft!=='object'||Array.isArray(draft))return;draft.entries=entries();draft.entries.push({ctx:{phase:[],hostile_present:false},act:{category:null,verb:'',role:'none'},n:1});index=draft.entries.length-1;change();render();});list.append(add);}
   }
  }else if(section==='roles'){list.innerHTML='<h2>物語の役割</h2><p>主人公・敵役・目標の結末を設定します。</p><p class="ux-note">初期物語の導入文は、通常の世界設定にあります。</p>';}
  else if(section==='files'){list.innerHTML='<h2>設定ファイル</h2>';Object.keys(files).forEach(p=>{const b=document.createElement('button');b.type='button';b.className='ux-rule';b.textContent=p;b.setAttribute('aria-pressed',String(p===file));b.addEventListener('click',()=>{if(dirty||busy){status.textContent='先に変更を保存するか、変更を戻してください。';return;}file=p;loaded();render();});list.append(b);});}
  else list.append($('[data-advanced-actions]').content.cloneNode(true));
  renderDetail();refresh();
 }
 async function api(url,body){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok){const error=Error(result.message||'保存内容を確認してください');error.status=response.status;throw error;}return result;}
 async function action(check){if(busy||blocked||!initial.editable)return;for(const el of detail.querySelectorAll('input,textarea,select'))if(!el.reportValidity())return;
  busy=true;root.querySelectorAll('input,textarea,select,button').forEach(e=>{e.dataset.disabled=String(e.disabled);e.disabled=true;});status.textContent=check?'設定を確認中…':'保存中…';
  try{if(section==='canon'){
    const content=JSON.stringify(draft,null,2),prefix='/api/genres/'+encodeURIComponent(genre.id)+'/';
    if(check){const result=await api(prefix+'check',{world_id:world.id,files:{[variant]:content},revision:genre.revision});status.textContent='設定の組み合わせを確認しました。'+(result.world_name||'')+(result.subjects!=null?'・登場人物 '+result.subjects+'人':'');}
    else{const result=await api(prefix+'save',{path:variant,content,revision:genre.files[variant].revision});genre=result;dirty=false;status.textContent='共有ジャンルの定石を保存しました';}
   }else{
    if(check){status.textContent='未保存の変更は保存後に世界とジャンルの組み合わせで確認できます。保存済みの設定を確認します。';const result=await api('/api/worlds/'+encodeURIComponent(world.id)+'/validate',{template_id:genre?.id||''});status.textContent='設定の組み合わせを確認しました。'+(result.world_name||'')+(result.subjects!=null?'・登場人物 '+result.subjects+'人':'');}
    else{const body={revision:world.revision,operation:section==='roles'?'roles':'file',target:section==='files'?file:null,values:section==='roles'?draft:{content:draft}};world=await api('/api/worlds/'+encodeURIComponent(world.id)+'/edit',body);if(section==='files')files[file]=draft;else{dirty=busy=false;location.reload();return;}dirty=false;status.textContent='世界の設定を保存しました';}
   }
  }catch(e){blocked=!e.status||e.status===409;status.textContent=e.status?e.message:'通信結果を確認できません。入力を控えてから再読み込みし、保存結果を確認してください。';}
  finally{busy=false;root.querySelectorAll('[data-disabled]').forEach(e=>{e.disabled=e.dataset.disabled==='true';delete e.dataset.disabled;});refresh();}
 }
 root.querySelectorAll('[data-advanced-section]').forEach(b=>b.addEventListener('click',()=>{if(dirty||busy){status.textContent='先に変更を保存するか、変更を戻してください。';return;}section=b.dataset.advancedSection;index=0;loaded();render();}));
 $('[data-advanced-reset]').addEventListener('click',()=>{loaded();render();status.textContent=blocked?'保存結果を確認するには再読み込みしてください。':'保存済みの内容に戻しました';});
 $('[data-advanced-save]').addEventListener('click',()=>action(false));$('[data-advanced-check]').addEventListener('click',()=>action(true));
 root.addEventListener('click',e=>{if(busy&&e.target.closest('a'))e.preventDefault();});addEventListener('beforeunload',e=>{if(dirty||busy){e.preventDefault();e.returnValue='';}});loaded();render();
})();
