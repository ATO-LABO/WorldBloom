(() => {
  'use strict';
  const root = document.querySelector('[data-world-draft]');
  if (!root) return;
  const $ = s => root.querySelector(s), form = $('[data-wd-input]');
  const name = form.elements.name, overview = form.elements.overview;
  const generateButton = $('[data-wd-generate]') || form.querySelector('button');
  const status = $('[data-wd-status]'), errors = $('[data-wd-errors]');
  const editor = $('[data-wd-json]'), confirm = $('[data-wd-confirm]');
  let record = null, token = null, busy = false, dirty = false, uncertain = false, epoch = 0;
  const goalRoot=$('[data-wd-goals]'),confirmRow=$('[data-wd-confirm-row]');
  const goalEditor=goalRoot&&window.WorldBloomDraftGoals?new window.WorldBloomDraftGoals.Editor(goalRoot,document,draft=>{
    editor.value=JSON.stringify(draft,null,2);markDirty();
    if(record?.state==='review'){
      try{summary(draft);}catch(_){$('[data-wd-summary]').replaceChildren();$('[data-wd-context]')?.replaceChildren();}
    }
  }):null;
  function editableRecord(){return !!(record&&['invalid','unknown'].includes(record.state)&&record.edit_revision&&record.draft);}
  function canValidate(){return record?.state==='review'||editableRecord();}
  function markDirty(){dirty=true;confirm.checked=false;status.textContent='下案に未検証の変更があります。修正した下案を確認してください。';controls();}
  function updateGoalEditor(){
    if(!goalEditor)return;
    try {goalEditor.render(editor.value.length<=128000?JSON.parse(editor.value):null);}catch(_){goalEditor.render(null);}
  }
  function carryToken(value){
    if(typeof URL==='undefined'||!window.location?.href||!window.history?.replaceState)return;
    const url=new URL(window.location.href);
    if(value)url.searchParams.set('draft',value);else url.searchParams.delete('draft');
    window.history.replaceState(window.history.state,'',url.pathname+url.search+url.hash);
  }
  function controls() {
    generateButton.disabled = busy || root.dataset.mode === 'waiting' || !!token;
    generateButton.hidden = ['review','adopted','adopting'].includes(record?.state)||editableRecord();
    $('[data-wd-adopt]').hidden = record?.state !== 'review'||dirty||!!record?.needs_revalidation;
    name.disabled = overview.disabled = busy || !!token;
    $('[data-wd-adopt]').disabled = busy || uncertain || dirty || !!record?.needs_revalidation || !confirm.checked || record?.state !== 'review';
    $('[data-wd-validate]').hidden=!canValidate()||(record?.state==='review'&&!dirty&&!record.needs_revalidation);
    $('[data-wd-validate]').disabled = busy || uncertain || !canValidate();
    $('[data-wd-cancel]').hidden = !token || ['adopted','cancelled','adopting'].includes(record?.state);
    $('[data-wd-cancel]').disabled = uncertain;
    $('[data-wd-retry]').hidden = !token || busy || uncertain || ['adopted','adopting','generating'].includes(record?.state);
    $('[data-wd-check]').hidden = !token || (!uncertain && !['generating','adopting','unknown'].includes(record?.state));
    $('[data-wd-check]').disabled = busy && !uncertain;
    confirm.disabled = busy || uncertain || dirty || !!record?.needs_revalidation || record?.state !== 'review';
    if(confirmRow)confirmRow.hidden=record?.state!=='review'||dirty||!!record?.needs_revalidation;
    editor.disabled = busy || uncertain;
    if(goalEditor)goalEditor.setDisabled(busy||uncertain||!canValidate());
  }
  async function api(path, body) {
    const response = await fetch('/api/world-drafts' + path, body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) {
      const error = new Error(value.message || '確認できませんでした');
      error.fields = value.field_errors || {}; error.known = response.status < 500 && response.status !== 409; throw error;
    }
    return value;
  }
  function table(title, headings, rows) {
    const section = document.createElement('section'), heading = document.createElement('h3');
    heading.textContent = title; section.append(heading);
    const table = document.createElement('table'), thead = document.createElement('thead'), hr = document.createElement('tr');
    headings.forEach(h => {const th = document.createElement('th');th.textContent=h;th.scope='col';hr.append(th);});thead.append(hr);table.append(thead);
    const tbody = document.createElement('tbody');
    rows.forEach(row => {const tr = document.createElement('tr');row.forEach((value,i) => {const td=document.createElement('td');td.textContent=String(value);td.dataset.label=headings[i];tr.append(td);});tbody.append(tr);});
    table.append(tbody);section.append(table);return section;
  }
  function summary(d) {
    const target=$('[data-wd-summary]');target.replaceChildren();
    const intro=document.createElement('p');intro.textContent=`${d.name} ／ 主人公：${d.protagonist} ／ 相手役：${d.antagonist}`+(goalEditor?'':`。導入：${d.initial_story}`);target.append(intro);
    const conditionText = c => ({reach:()=>`${c.place}に到着する`,acquire:()=>`${c.item}を所持する`,deliver:()=>`${c.item}を所持して${c.place}にいる`,meet:()=>`${c.person}と生存して同じ場所にいる`,befriend:()=>`${c.person}との双方の親しさが${c.threshold}以上になる`,defeat:()=>`${c.person}が戦闘不能または死亡する`,survive:()=>`${c.day}日目の夜まで生存する`,discover:()=>`${c.fact}を知る`}[c.type]?.() || '条件を確認してください');
    const goalText = g => g.description !== undefined
      ? g.description + (g.conditions.length ? ' ／ 達成条件（すべて同時に成立）：'+g.conditions.map(conditionText).join('、') : ' ／ 自動判定なし。達成は内容を読んで確認します。')
      : g.target ? g.target+'を'+g.deliver_to+'へ届ける' : '未設定';
    if(!goalEditor){
      target.append(table('人物と目標',['名前・人物像','紹介','開始位置','目標'],d.people.map(p=>[p.name+'／'+p.personality,p.description,p.entry,goalText(p.goal)])));
      target.append(table('場所',['場所','説明'],d.places.map(p=>[p.name,p.description])));
    }
    const context=$('[data-wd-context]')||target;if(context!==target)context.replaceChildren();
    if(!goalEditor){
      const relations=d.people.flatMap(p=>p.relations.map(r=>[p.name+' → '+r.person,r.label || 'ラベルなし',r.affinity,r.awareness]));
      context.append(table('人物間の関係',['関係の向き','ラベル','親しさ（−1〜1）','認知（0〜1）'],relations));
    }
    if(!goalEditor)context.append(table('品物と経路',['種類','内容'],[...d.items.map(i=>['品物',i.name+'／所持：'+i.holder]),...d.routes.map(r=>['経路',r.from+' → '+r.to])]));
    if(d.facts?.length)context.append(table('調べて得る情報',['名前','内容','調べる場所'],d.facts.map(f=>[f.name,f.description,f.place])));
  }
  function show(value) {
    record=value;uncertain=false;confirm.checked=false;dirty=false;
    if(['adopted','cancelled'].includes(value.state)){sessionStorage.removeItem('wb-new-world-draft');carryToken(null);}
    if(value.name)name.value=value.name;
    if(value.overview)overview.value=value.overview;
    errors.textContent=Object.entries(value.errors||{}).map(([k,v])=>`${k}：${v}`).join('\n');
    $('[data-wd-review]').hidden=value.state!=='review'&&!editableRecord();
    if(value.state==='review') {editor.value=JSON.stringify(value.draft,null,2);summary(value.draft);updateGoalEditor();status.textContent=value.needs_revalidation?'確認方法が更新されています。「下案を確認する」で再確認してください。':(value.summary?.alignment==='reviewed'?'概要・目標・達成条件の整合性と、開始時点で未達成であることを確認しました。':'下案の構造・参照とエンジン入力を確認しました。')+' 内容を確認して採用してください。';}
    else if(editableRecord()){editor.value=JSON.stringify(value.draft,null,2);$('[data-wd-summary]').replaceChildren();$('[data-wd-context]')?.replaceChildren();updateGoalEditor();status.textContent=value.state==='unknown'?'確認結果が不明です。下案を見直して、必要なら明示的に再確認できます。自動再送はしません。':'下案に指摘があります。内容を修正し「下案を確認する」を押してください。生成し直す必要はありません。';}
    else status.textContent={generating:'下案を作っています。取り消して採用を無効にできます。',invalid:'下案が検証に通りませんでした。世界は保存されていません。',unknown:'生成結果を確定できませんでした。自動再送はしません。',cancelled:'下案を取り消しました。新規世界は保存していません。',adopting:'保存結果の確認が必要です。再送せず状態を確認してください。',adopted:'新しい世界として保存しました。実行は開始していません。'}[value.state] || value.state;
    if(value.state==='adopted') {const link=document.createElement('a');link.href='/worlds/'+encodeURIComponent(value.world_id);link.textContent='保存した世界設定を開く →';$('[data-wd-result]').replaceChildren(link);}
    controls();
  }
  function failed(error) {
    errors.textContent=Object.entries(error.fields||{}).map(([k,v])=>`${k}：${v}`).join('\n') || error.message;
    if(!error.known) {uncertain=true;status.textContent='通信結果を確認できません。再送せず「状態を確認」を押してください。';}
    controls();
  }
  form.addEventListener('submit',async e=>{
    e.preventDefault();if(busy||token||!form.reportValidity()||root.dataset.mode==='waiting')return;
    token=crypto.randomUUID().replaceAll('-','');sessionStorage.setItem('wb-new-world-draft',token);carryToken(token);const run=++epoch;
    record={state:'generating'};busy=true;errors.textContent='';status.textContent='下案を作成し、概要・目標・達成条件を確認しています。まだ世界には保存していません。';controls();
    try {const r=await api('',{request_id:token,name:name.value,overview:overview.value});if(run===epoch)show(r);}
    catch(error){if(run===epoch){if(error.known){token=null;record=null;sessionStorage.removeItem('wb-new-world-draft');}failed(error);}}finally{if(run===epoch){busy=false;controls();}}
  });
  $('[data-wd-cancel]').addEventListener('click',async()=>{
    if(!token||uncertain)return;const run=++epoch;busy=true;controls();
    try {const r=await api('/'+token+'/cancel',{});if(run===epoch)show(r);}catch(error){failed(error);}finally{busy=false;controls();}
  });
  $('[data-wd-retry]').addEventListener('click',async()=>{
    if(busy||uncertain||!token)return;
    if(dirty&&typeof window.confirm==='function'&&!window.confirm('修正中の内容を破棄して、入力から作り直しますか？'))return;
    busy=true;controls();
    try {if(record?.state!=='cancelled')await api('/'+token+'/cancel',{});token=null;record=null;carryToken(null);++epoch;dirty=false;confirm.checked=false;$('[data-wd-review]').hidden=true;errors.textContent='';status.textContent='前の下案を取り消しました。入力を確認し「下案を作る」を押してください。';}
    catch(error){failed(error);}finally{busy=false;controls();}
  });
  $('[data-wd-check]').addEventListener('click',async()=>{
    if(!token||(busy&&!uncertain))return;
    if(dirty&&typeof window.confirm==='function'&&!window.confirm('保存済みの状態を読み直すと、修正中の内容は破棄されます。読み直しますか？'))return;
    const run=++epoch;busy=true;controls();
    try{const r=await api('/'+token);if(run===epoch)show(r);}catch(error){if(run===epoch)failed(error);}finally{if(run===epoch){busy=false;controls();}}
  });
  editor.addEventListener('input',()=>{markDirty();updateGoalEditor();});
  confirm.addEventListener('change',controls);
  $('[data-wd-validate]').addEventListener('click',async()=>{
    if(busy||uncertain||!canValidate())return;
    let draft;try{draft=JSON.parse(editor.value);}catch(_){errors.textContent='JSONの書式を確認してください。入力は残っています。';return;}
    const run=++epoch;busy=true;status.textContent='修正した下案の構造・達成条件と、概要との整合性を確認しています。実モデルでは数分かかる場合があります。';controls();
    try{const r=await api('/'+token+'/validate',{revision:record.edit_revision||record.revision,draft});if(run===epoch)show(r);}catch(error){if(run===epoch)failed(error);}finally{if(run===epoch){busy=false;controls();}}
  });
  $('[data-wd-adopt]').addEventListener('click',async()=>{
    if(busy||uncertain||dirty||record?.needs_revalidation||!confirm.checked||record?.state!=='review')return;
    busy=true;controls();try{show(await api('/'+token+'/adopt',{revision:record.revision}));}catch(error){failed(error);}finally{busy=false;controls();}
  });
  window.addEventListener('beforeunload',e=>{if(token&&!['adopted','cancelled'].includes(record?.state)){e.preventDefault();e.returnValue='';}});
  token=sessionStorage.getItem('wb-new-world-draft');
  if(typeof URLSearchParams!=='undefined'){
    const requested=new URLSearchParams(window.location?.search||'').get('draft');
    if(requested&&/^[a-f0-9]{32}$/.test(requested)){token=requested;sessionStorage.setItem('wb-new-world-draft',token);}
  }
  if(token && /^[a-f0-9]{32}$/.test(token)) {
    uncertain=true;record={state:'generating'};
    status.textContent='前の下案要求があります。「状態を確認」で保存済みの状態を読み直してください。';
  } else token=null;
  controls();
})();
