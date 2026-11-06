(() => {
 'use strict';const root=document.querySelector('[data-world-advanced]');if(!root)return;
 const initial=JSON.parse(root.dataset.initial),$=s=>root.querySelector(s),list=$('[data-advanced-list]'),detail=$('[data-advanced-detail]'),status=$('[data-advanced-status]');
 const clone=v=>JSON.parse(JSON.stringify(v)),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const reading=$('[data-advanced-reading]'),dialog=$('[data-advanced-dialog]'),message=$('[data-advanced-message]');
 let activeTopic=null,returnFocus=null;
 let world=initial.world,genre=initial.genre,files=initial.files,section=['roles','concerns','objectives','responses','planning','genres','canon','actions','files'].includes(location.hash.slice(1))?location.hash.slice(1):'genres',index=0,variant='canon.yaml',file='world.yaml',dirty=false,busy=false,blocked=false;
 let draft=null;
 if(section==='roles'){const edit=new URLSearchParams(location.search).get('edit');location.replace('/worlds/'+encodeURIComponent(world.id)+(edit?'?edit='+(edit==='roles:2'?'target-ending':'roles'):'')+'#screen='+(edit==='roles:2'?'overview':'people'));return;}
 const labels={phase:'段階',hostile_present:'敵対者がいる',objective:'目的の品',vitality:'生存状態',stance:'態度',category:'カテゴリ',verb:'行動',role:'相手との関係',n:'重み'};
 const terms={hostile:'敵対',self:'自分',neutral:'中立',alive:'生存',dead:'死亡',none:'指定なし',friendly:'友好',give_item:'品物を渡す',craft:'作る',fight:'戦う',move:'移動する',rest:'休む',observe:'調べる',persuade:'説得する',cooperate:'協力する',ally:'仲間',ground:'地面',unknown:'不明',downed:'倒れている'};
 const text=v=>Array.isArray(v)?(v.length?v.join('・'):'指定なし'):v===null?'指定なし':v===true?'はい':v===false?'いいえ':terms[v]||String(v??'未設定');
 const entries=()=>Array.isArray(draft?.entries)?draft.entries:[];
 function loaded(){draft=section==='genres'?clone(world.story_genres||[]):section==='planning'?clone(world.world.planning??window.WorldBloomPlanning.empty()):section==='objectives'?clone(world.world.goal_rules??window.WorldBloomObjectives.empty()):section==='responses'?clone(world.world.responses??window.WorldBloomResponses.empty()):section==='concerns'?{version:2,conditions:clone(world.world.concerns?.conditions??[])}:section==='canon'?clone(genre?.files[variant]?.value??{entries:[]}):section==='roles'?{protagonist:world.world.protagonist||'',antagonist:world.world.antagonist||'',target_ending:Array.isArray(world.world.target_ending)?clone(world.world.target_ending):world.world.target_ending?[world.world.target_ending]:[]}:files[file]??'';dirty=false;}
 function refresh(){$('[data-advanced-check]').hidden=section==='genres';const can=initial.editable&&section!=='actions'&&(section!=='canon'||!!genre);$('[data-advanced-save]').disabled=!can||!dirty||busy||blocked;$('[data-advanced-check]').disabled=!can||busy||blocked;root.querySelectorAll('[data-advanced-close]').forEach(b=>b.disabled=busy);root.querySelectorAll('[data-advanced-section]').forEach(b=>b.setAttribute('aria-current',b.dataset.advancedSection===section?'page':'false'));}
 function change(){dirty=true;message.textContent='';refresh();}
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
  if(section==='genres'){
   detail.innerHTML='<h2>この世界で使うジャンル</h2><p>共通の行動は常に有効です。追加するジャンルは複数選べます。</p>';const field=document.createElement('fieldset');field.dataset.genreChoices='';field.innerHTML='<legend>ジャンルの追加・解除</legend><label class="ux-inline"><input type="checkbox" checked disabled> 共通（常に有効）</label>';for(const g of initial.story_genre_catalog){const label=document.createElement('label');label.className='wa-genre-option';const check=document.createElement('input');check.type='checkbox';check.dataset.storyGenre=g.id;check.checked=draft.includes(g.id);check.disabled=!initial.editable;check.addEventListener('change',()=>{draft=check.checked?[...draft,g.id]:draft.filter(id=>id!==g.id);change();});const text=document.createElement('span');text.innerHTML='<strong>'+esc(g.name)+'</strong><small>'+esc(g.description)+'</small>';label.append(check,text);field.append(label);}detail.append(field);
  }else if(section==='canon'){
   const row=entries()[index];detail.innerHTML='<h2>選んだ定石</h2><p class="ux-note">ジャンル「'+esc(genre?.id||'未設定')+'」の共有設定です。このジャンルを使う他の世界にも影響します。保存済みの実験は変わりません。</p>';
   if(!row){detail.insertAdjacentHTML('beforeend','<p>定石を選ぶか、追加してください。</p>');return;}
   if(!row.ctx||typeof row.ctx!=='object'||Array.isArray(row.ctx)||!row.act||typeof row.act!=='object'||Array.isArray(row.act)){detail.insertAdjacentHTML('beforeend','<p>この定石はフォームで扱えない形式です。ジャンル編集の設定ファイルで確認してください。</p>');return;}
   for(const [key,title] of [['ctx','適用する状況'],['act','選ぶ行動']]){const group=document.createElement('fieldset');group.innerHTML='<legend>'+title+'</legend>';for(const k of Object.keys(row[key]))control(row[key],k,group);if(key==='ctx'){const fold=document.createElement('details');fold.innerHTML='<summary>適用する状況を編集</summary><p>'+esc(ruleText(row))+'</p>';fold.append(group);detail.append(fold);}else detail.append(group);}if('n'in row)control(row,'n',detail);
   detail.insertAdjacentHTML('beforeend','<p class="ux-readback" data-readback>'+esc(ruleText(row)+' → '+ruleTitle(row))+'</p>');
   const canon=document.createElement('details');canon.innerHTML='<summary>定石の早見表を確認</summary>';canon.append($('[data-advanced-canon]').content.cloneNode(true));detail.append(canon);if(genre)detail.insertAdjacentHTML('beforeend','<a href="/genres/'+encodeURIComponent(genre.id)+'">共有ルールの原本を編集 →</a>');
  }else if(section==='objectives'){
   window.WorldBloomObjectives.render(detail,draft,world,initial.editable,change);
  }else if(section==='planning'){
   window.WorldBloomPlanning.render(detail,draft,world,initial.editable,change);
  }else if(section==='responses'){
   window.WorldBloomResponses.render(detail,draft,world,initial.editable,change);
  }else if(section==='concerns'){
   window.WorldBloomConcerns.render(detail,draft,world,initial.editable,change);
  }else if(section==='roles'){
   detail.innerHTML='<h2>役割と目標の結末</h2><p>この世界の登場人物から選びます。</p>';
   for(const [key,title] of [['protagonist','主人公'],['antagonist','敵役']]){const label=document.createElement('label');label.textContent=title;const select=document.createElement('select');select.setAttribute('aria-label',title);select.innerHTML='<option value="">未設定</option>'+world.people.map(p=>'<option value="'+esc(p.id)+'">'+esc(p.id)+'</option>').join('');select.value=draft[key];select.disabled=!initial.editable;select.addEventListener('change',()=>{draft[key]=select.value;change();});label.append(select);detail.append(label);}
   const field=document.createElement('fieldset');field.innerHTML='<legend>目標の結末</legend>';for(const ending of world.world.ending||[]){const label=document.createElement('label');label.className='ux-inline';const check=document.createElement('input');check.type='checkbox';check.checked=draft.target_ending.includes(ending.id);check.disabled=!initial.editable;check.addEventListener('change',()=>{draft.target_ending=check.checked?[...draft.target_ending,ending.id]:draft.target_ending.filter(v=>v!==ending.id);change();});label.append(check,document.createTextNode(ending.label||ending.id));field.append(label);}detail.append(field);

  }else if(section==='files'){
   detail.innerHTML='<h2>'+esc(file)+'</h2><p class="ux-note">世界の設定ファイルを直接編集します。別画面で変更されていた場合は保存を止めます。</p>';const label=document.createElement('label');label.textContent='設定内容';const area=document.createElement('textarea');area.className='ux-code';area.spellcheck=false;area.value=draft;area.readOnly=!initial.editable;area.addEventListener('input',()=>{draft=area.value;change();});label.append(area);detail.append(label);
  }else{detail.innerHTML='<h2>行動の詳細</h2><p>左の行動図鑑を展開して、条件と結果を確認できます。</p>'+(genre?'<a href="/genres/'+encodeURIComponent(genre.id)+'">ジャンルの行動を編集 →</a>':'');}
 }
 // Reading and editing use the same field renderers. Only the chosen top-level
 // topic is exposed in the dialog; the saved snapshot is never mutated locally.
 function topics(){
  const children=[...detail.children];
  if(section==='genres'||section==='canon'||section==='files')return [{key:'whole',title:section==='genres'?'この世界で使うジャンル':section==='files'?file:'選んだ定石',nodes:children}];
  let nodes=[];
  if(section==='roles')nodes=children.filter(n=>n.matches('label,fieldset'));
  if(section==='concerns')nodes=children.filter(n=>n.hasAttribute('data-concern-index'));
  if(section==='objectives')nodes=children.filter(n=>n.hasAttribute('data-objective-index'));
  if(section==='planning')nodes=children.filter(n=>n.matches('fieldset,label'));
  if(section==='responses')nodes=children.filter(n=>n.matches('fieldset,details'));
  return nodes.map((node,i)=>({key:section+':'+i,title:node.querySelector(':scope > legend,:scope > summary,:scope > span')?.textContent||[...node.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim()||'設定',nodes:[node]}));
 }
 function readNode(source){
  const node=source.cloneNode(true),original=[...source.querySelectorAll('input,select,textarea')],copies=[...node.querySelectorAll('input,select,textarea')];
  original.forEach((field,i)=>{
   const copy=copies[i],label=copy.closest('label');
   if(field.type==='checkbox'||field.type==='radio'){
    if(!field.checked){label?.remove();return;}
    const value=document.createElement('span');value.textContent='✓ ';copy.replaceWith(value);return;
   }
   if(label&&/^追加する/.test(label.querySelector('span')?.textContent||'')){label.remove();return;}
   const value=document.createElement(field.tagName==='TEXTAREA'?'pre':'span');value.className='wa-value';value.textContent=field.tagName==='SELECT'?field.selectedOptions[0]?.textContent||'未設定':field.value||'未設定';copy.replaceWith(value);
  });
  node.querySelectorAll('button,h2').forEach(n=>n.remove());
  if(source.matches('label')){const value=node.querySelector('.wa-value');if(value){const line=document.createElement('p');line.append(value);return line;}}
  if(node.matches('details'))node.open=true;
  return node;
 }
 function pencil(title,fn){
  const b=document.createElement('button');b.type='button';b.className='ui-button';b.dataset.advancedEdit='';b.setAttribute('aria-label',title+'を編集');b.innerHTML='<svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="m16.9 3.9 3.2 3.2-11 11-4.3 1.1 1.1-4.3 11-11Zm1.4-1.4a2 2 0 0 1 2.8 0l.4.4a2 2 0 0 1 0 2.8l-.7.7-3.2-3.2.7-.7Z"/></svg><span>編集</span>';b.disabled=!initial.editable||blocked;b.addEventListener('click',()=>fn(b));return b;
 }
 function renderReading(){
  reading.replaceChildren();reading.hidden=section==='actions';
  if(section==='actions')return;
  const heading=document.createElement('h2');heading.textContent=root.querySelector('[data-advanced-section="'+section+'"]')?.textContent||'設定';reading.append(heading);
  if(section==='canon'){const p=document.createElement('p');p.className='ui-notice';p.textContent='共有の基本ルールの設定です。同じ基本ルールを使う他の世界にも反映されます。';reading.append(p);}
  const all=topics();
  for(const topic of all){
   const card=document.createElement('section');card.className='wa-topic';const head=document.createElement('header');head.className='ui-section-head';const title=document.createElement('h3');title.textContent=topic.title;head.append(title);
   if(section!=='canon'){const edit=pencil(topic.title,b=>openTopic(topic.key,b));edit.dataset.topic=topic.key;head.append(edit);}
   card.append(head);for(const node of topic.nodes)if(!node.matches('h2'))card.append(readNode(node));reading.append(card);
  }
  if(section==='roles'||section==='objectives'){
   const extra=document.createElement('details');extra.innerHTML='<summary>人物の能力・秘密・伏線を確認</summary>';extra.append($('[data-advanced-readouts]').content.cloneNode(true));
   const choices=document.createElement('div');choices.className='wa-person-links';
   for(const sheet of extra.querySelectorAll('dialog[id]')){
        const button=document.createElement('button');
        button.type='button';
        button.dataset.sheet=sheet.id;
        button.textContent=sheet.querySelector('h4')?.textContent||sheet.getAttribute('aria-label');
        button.addEventListener('click',()=>{if(!sheet.open)sheet.showModal();});
        sheet.addEventListener('click',event=>{
          if(event.target===sheet||event.target.closest('[data-close-dialog]'))sheet.close();
        });
        sheet.addEventListener('close',()=>button.focus({preventScroll:true}));
        choices.append(button);
      }
   extra.insertBefore(choices,extra.children[1]);reading.append(extra);
  }
  // Empty states and unsupported forms retain their renderer's explanation.
  if(!all.length){for(const node of detail.children)if(!node.matches('button,h2'))reading.append(readNode(node));}
  if(['concerns','objectives'].includes(section)){
   const add=detail.querySelector(':scope > button:last-of-type');
   if(add){const b=pencil(add.textContent,opener=>{
    loaded();renderDetail();const button=detail.querySelector(':scope > button:last-of-type');button?.click();
    const topic=topics().at(-1);if(topic){openTopic(topic.key,opener,true);}
   });b.textContent=add.textContent;b.disabled=add.disabled||!initial.editable||blocked;reading.append(b);}
  }
 }
 function projectTopic(){
  if(!dialog.open||!activeTopic)return;
  const selected=topics().find(t=>t.key===activeTopic),keep=new Set(selected?.nodes||[]);
  if(section==='responses'&&activeTopic==='responses:0'){const upgrade=detail.querySelector(':scope > button');if(upgrade)keep.add(upgrade);}
  for(const node of detail.children)node.hidden=!keep.has(node);
  if(activeTopic==='removed'){
   let note=detail.querySelector('[data-wa-removed]');if(!note){note=document.createElement('p');note.dataset.waRemoved='';note.textContent='この項目を削除します。保存するまで元の設定は変わりません。';detail.append(note);}note.hidden=false;
  }
 }
 function openTopic(key,opener,keepDraft=false){
  if(!initial.editable||busy||blocked)return;
  if(!keepDraft){loaded();renderDetail();}
  activeTopic=key;returnFocus=opener;const topic=topics().find(t=>t.key===key);if(!topic)return;
  $('#wa-dialog-title').textContent=topic.title+'を編集';message.textContent='';$('[data-advanced-check]').textContent=['roles','files'].includes(section)?'保存済みの設定を確認':'設定を確認';
  $('[data-advanced-scope]').textContent=section==='canon'?'共有の基本ルールの設定です。同じ基本ルールの他の世界にも反映されます。':'保存すると、この世界の次の実行から使われます。';
  if(keepDraft)dirty=true;
  dialog.showModal();projectTopic();refresh();[...dialog.querySelectorAll('input,select,textarea')].find(n=>!n.disabled&&n.getClientRects().length)?.focus();
 }
 function restoreFocus(){const key=returnFocus?.dataset.topic;const target=key?reading.querySelector('[data-topic="'+key+'"]'):reading.querySelector('[data-advanced-edit]');(target||root.querySelector('[data-advanced-section="'+section+'"]'))?.focus({preventScroll:true});}
 function cancelTopic(){
  if(busy)return;dialog.close();activeTopic=null;loaded();render();status.textContent=blocked?'保存結果を確認するには再読み込みしてください。':'保存された設定を表示しています';restoreFocus();
 }
 root.querySelectorAll('[data-advanced-close]').forEach(b=>b.addEventListener('click',cancelTopic));
 dialog.addEventListener('cancel',e=>{e.preventDefault();cancelTopic();});
 dialog.addEventListener('click',e=>{if(e.target!==dialog)return;const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)cancelTopic();});
 dialog.addEventListener('keydown',e=>{
  if(e.key!=='Tab')return;const focus=[...dialog.querySelectorAll('button,input,select,textarea,a[href],summary')].filter(n=>!n.disabled&&n.getClientRects().length);const first=focus[0],last=focus.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
 });
 detail.addEventListener('click',e=>{const text=e.target.closest('button')?.textContent;if(['この条件を外す','この判定を外す'].includes(text))activeTopic='removed';},true);
 new MutationObserver(()=>projectTopic()).observe(detail,{childList:true});

 function render(){list.replaceChildren();
  const category=section==='objectives'?'people':section==='roles'?'overview':section==='files'?'files':'rules';
  root.querySelectorAll('[data-settings-category]').forEach(a=>a.setAttribute('aria-current',a.dataset.settingsCategory===category?'page':'false'));
  $('[data-advanced-sections]').hidden=category==='files';
  root.querySelectorAll('[data-advanced-section]').forEach(b=>b.hidden=category==='rules'?['roles','objectives','files'].includes(b.dataset.advancedSection):b.dataset.advancedSection!==section);
  if(category==='people'){const a=document.createElement('a');a.className='ui-button';a.href='/worlds/'+encodeURIComponent(world.id)+'#screen=people';a.textContent='人物・目標・価値観に戻る';list.append(a);}

  if(section==='canon'){
   list.innerHTML='<h2>状況ごとの定石</h2>';
   if(!genre){list.insertAdjacentHTML('beforeend','<p>ジャンルが未設定です。</p>');}
   else{const select=document.createElement('select');select.setAttribute('aria-label','定石の役割');select.innerHTML='<option value="canon.yaml">主人公側</option><option value="canon.antagonist.yaml">敵役側</option>';select.value=variant;select.addEventListener('change',()=>{if(dirty||busy){select.value=variant;status.textContent='先に変更を保存するか、変更を戻してください。';return;}variant=select.value;index=0;loaded();render();});list.append(select);
    if(genre.files[variant]?.error)list.insertAdjacentHTML('beforeend','<p>設定の書式を読み取れません。ジャンル編集で確認してください。</p>');
    entries().forEach((row,i)=>{const b=document.createElement('button');b.className='ux-rule';b.type='button';b.setAttribute('aria-pressed',String(i===index));b.innerHTML='<strong>'+esc(ruleTitle(row))+'</strong><small>'+esc(ruleText(row))+'</small>';b.addEventListener('click',()=>{index=i;render();});list.append(b);});

   }
  }else if(section==='roles'){list.innerHTML='<h2>物語の役割</h2><p>主人公・敵役・目標の結末を設定します。</p><p class="ux-note">初期物語の導入文は、通常の世界設定にあります。</p>';}
  else if(section==='files'){list.innerHTML='<h2>設定ファイル</h2>';Object.keys(files).forEach(p=>{const b=document.createElement('button');b.type='button';b.className='ux-rule';b.textContent=p;b.setAttribute('aria-pressed',String(p===file));b.addEventListener('click',()=>{if(dirty||busy){status.textContent='先に変更を保存するか、変更を戻してください。';return;}file=p;loaded();render();});list.append(b);});}
  else if(section==='objectives'){list.append(document.createTextNode('達成条件・失敗確定条件・判定期間を一組として定義します。各人物は対応する条件を人物設定で選べます。'));}
  else if(section==='planning'){list.textContent='最終目的から準備を組み立て、失敗や新しい情報に応じて手順を見直します。';}
  else if(section==='responses'){list.textContent='知らせ、現場での対処、連絡手段、条件を守る期間を設定します。';}
  else if(section==='concerns'){list.textContent='世界で共有する条件を定義します。重視する条件・優先順位・譲れない条件は、登場人物から編集できます。';}
  else if(section==='genres')list.textContent='この世界で使う行動の範囲を選びます。';
  else list.append($('[data-advanced-actions]').content.cloneNode(true));
  list.hidden=!['canon','files','actions','objectives'].includes(section);root.dataset.advancedView=section;
  renderDetail();renderReading();refresh();
 }
 async function api(url,body){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok){const error=Error(result.message||'保存内容を確認してください');error.status=response.status;throw error;}return result;}
 async function action(check){if(busy||blocked||!initial.editable)return;for(const el of detail.querySelectorAll('input,textarea,select'))if(el.getClientRects().length&&!el.reportValidity())return;
  busy=true;root.querySelectorAll('input,textarea,select,button').forEach(e=>{e.dataset.disabled=String(e.disabled);e.disabled=true;});message.textContent=check?'設定を確認中…':'保存中…';
  let saved=false;
  try{if(section==='genres'){
    if(!check){world=await api('/api/worlds/'+encodeURIComponent(world.id)+'/edit',{revision:world.revision,operation:'story-genres',target:null,values:{genres:draft}});dirty=false;status.textContent='ジャンルを保存しました';}
   }else if(['concerns','responses','objectives','planning'].includes(section)){
    const result=await api('/api/worlds/'+encodeURIComponent(world.id)+'/edit',{revision:world.revision,operation:check?(section==='objectives'?'goal-rules':section)+'-check':section==='objectives'?'goal-rules':section,target:null,values:{definition:section==='planning'?window.WorldBloomPlanning.definition(draft):section==='objectives'?(draft.definitions.length?draft:null):section==='responses'?(Object.keys(draft.state).length?draft:null):(draft.conditions.length?draft:null)}});
    if(check){status.textContent=section==='planning'?'未保存の再計画設定を確認しました。':section==='objectives'?'未保存の達成・失敗・期間の定義を確認しました。':section==='responses'?'未保存の対処設定を確認しました。':'未保存の条件を確認しました。';}else{world=result;dirty=false;status.textContent='世界の設定を保存しました';}
   }else if(section==='canon'){
    const content=JSON.stringify(draft,null,2),prefix='/api/genres/'+encodeURIComponent(genre.id)+'/';
    if(check){const result=await api(prefix+'check',{world_id:world.id,files:{[variant]:content},revision:genre.revision});status.textContent='設定の組み合わせを確認しました。'+(result.world_name||'')+(result.subjects!=null?'・登場人物 '+result.subjects+'人':'');}
    else{const result=await api(prefix+'save',{path:variant,content,revision:genre.files[variant].revision});genre=result;dirty=false;status.textContent='共有する基本ルールの定石を保存しました';}
   }else{
    if(check){status.textContent='保存済みの設定を確認します。';const result=await api('/api/worlds/'+encodeURIComponent(world.id)+'/validate',{template_id:genre?.id||''});status.textContent='保存済みの設定を確認しました。編集中の内容は含みません。'+(result.world_name||'')+(result.subjects!=null?'・登場人物 '+result.subjects+'人':'');}
    else{const body={revision:world.revision,operation:section==='roles'?'roles':'file',target:section==='files'?file:null,values:section==='roles'?draft:{content:draft}};world=await api('/api/worlds/'+encodeURIComponent(world.id)+'/edit',body);if(section==='files')files[file]=draft;dirty=false;status.textContent='世界の設定を保存しました';}
   }
   saved=!check;if(saved){
    // Refresh exact file text too: a subsequent raw-file edit must never start
    // from the pre-save version or silently discard authored comments.
    try{const response=await fetch(location.pathname+location.search,{cache:'no-store'});if(!response.ok)throw Error();const doc=new DOMParser().parseFromString(await response.text(),'text/html');const fresh=JSON.parse(doc.querySelector('[data-world-advanced]').dataset.initial);world=fresh.world;genre=fresh.genre;files=fresh.files;}catch{blocked=true;status.textContent='保存しました。最新の内容を確認するには再読み込みしてください。';}
   }if(check)message.textContent=status.textContent;
  }catch(e){blocked=!e.status||e.status===409;status.textContent=e.status?e.message:'通信結果を確認できません。入力を控えてから再読み込みし、保存結果を確認してください。';message.textContent=status.textContent;}
  finally{busy=false;root.querySelectorAll('[data-disabled]').forEach(e=>{e.disabled=e.dataset.disabled==='true';delete e.dataset.disabled;});refresh();if(saved){dialog.close();activeTopic=null;loaded();render();restoreFocus();}}
 }
 root.querySelectorAll('[data-advanced-section]').forEach(b=>b.addEventListener('click',()=>{if(dirty||busy){status.textContent='先に変更を保存するか、変更を戻してください。';return;}section=b.dataset.advancedSection;history.replaceState(null,'','#'+section);index=0;loaded();render();}));
 addEventListener('hashchange',()=>{const next=location.hash.slice(1);if(next===section||!['roles','concerns','objectives','responses','planning','genres','canon','actions','files'].includes(next))return;if(dirty||busy){history.replaceState(null,'','#'+section);status.textContent='先に変更を保存するか、キャンセルしてください。';return;}section=next;index=0;loaded();render();});
 $('[data-advanced-form]').addEventListener('submit',e=>{e.preventDefault();action(false);});$('[data-advanced-check]').addEventListener('click',()=>action(true));
 root.addEventListener('click',e=>{if(busy&&e.target.closest('a'))e.preventDefault();});addEventListener('beforeunload',e=>{if(dirty||busy){e.preventDefault();e.returnValue='';}});loaded();render();const requested=new URLSearchParams(location.search).get('edit');if(requested){const opener=[...reading.querySelectorAll('[data-topic]')].find(n=>n.dataset.topic===requested);if(opener)openTopic(requested,opener);}
})();
