(() => {
  'use strict';
  const root = document.querySelector('[data-world-draft]');
  if (!root) return;
  const $ = s => root.querySelector(s), form = $('[data-wd-input]');
  const name = form.elements.name, overview = form.elements.overview;
  const authoring=window.WorldBloomAuthoring?.connect(root),getAuthoring=()=>authoring?.get()||null;
  const timeExplicit=$('[data-wd-time-explicit]'),timeFields=$('[data-wd-time-fields]'),timeDays=$('[data-wd-time-days]'),timeSlots=$('[data-wd-time-slots]');
  const generateButton = $('[data-wd-generate]') || form.querySelector('button');
  const status = $('[data-wd-status]'), errors = $('[data-wd-errors]');
  const editor = $('[data-wd-json]'), confirm = $('[data-wd-confirm]');
  let record = null, token = null, busy = false, dirty = false, uncertain = false, rawInvalid = false, epoch = 0;
  let checkpoint=null;
  const goalRoot=$('[data-wd-goals]'),confirmRow=$('[data-wd-confirm-row]');
  const workspaceRoot=$('[data-wd-workspace]');let workspace=null,editing=null;
  if(workspaceRoot&&window.WorldBloomDraftWorkspace&&window.WorldBloomDraftEditing){
    root.classList.add('has-workspace');
    workspace=new window.WorldBloomDraftWorkspace(workspaceRoot,{onChange:next=>{editor.value=JSON.stringify(next,null,2);markDirty();workspace.render(next);},beforeNavigate:()=>window.WorldBloomDraftForms?.canLeave()!==false&&(editing?.canLeave()??true),afterRender:(target,content)=>editing?.mount(target,content)});
    editing=new window.WorldBloomDraftEditing({getDraft:()=>JSON.parse(editor.value),
      onChange:(draft,target)=>{editor.value=JSON.stringify(draft,null,2);markDirty();workspace.render(draft);if(target)workspace.select(target.section,target.entity);},
      requestRevision:async body=>{if(!token)throw new Error('先に下案を確認して、AI修正の対象となる版を保存してください。');busy=true;controls();try{return await api('/'+token+'/revise',{revision:record.edit_revision||record.revision,...body});}finally{busy=false;controls();}},
      applyRevision:async(preview,base,candidate)=>{busy=true;controls();try{show(await api('/'+token+'/apply-revision',{revision:record.edit_revision||record.revision,request_id:preview.request_id,draft:base,candidate}));}catch(error){failed(error);throw error;}finally{busy=false;controls();}},
      refresh:()=>workspace.render(JSON.parse(editor.value)),
      notice:root.dataset.mode==='live'?undefined:'この起動は表示用サンプルです。実AI修正は利用できません。実モデル接続時に修正案を作れます。'});
  }
  const goalEditor=!workspace&&goalRoot&&window.WorldBloomDraftGoals?new window.WorldBloomDraftGoals.Editor(goalRoot,document,draft=>{
    editor.value=JSON.stringify(draft,null,2);markDirty();
    if(record?.state==='review'){
      try{summary(draft);}catch(_){$('[data-wd-summary]').replaceChildren();$('[data-wd-context]')?.replaceChildren();}
    }
  }):null;
  function editableRecord(){return !!(record&&['invalid','unknown'].includes(record.state)&&record.edit_revision&&record.draft);}
  function canValidate(){return record?.state==='working'||record?.state==='review'||editableRecord();}
  function markDirty(){dirty=true;confirm.checked=false;status.textContent='下案に未検証の変更があります。修正した下案を確認してください。';controls();}
  function updateGoalEditor(){
    if(workspace){
      const focused=document.activeElement===editor,start=editor.selectionStart,end=editor.selectionEnd,scroll=editor.scrollTop;
      try{workspace.render(JSON.parse(editor.value));rawInvalid=false;if(errors.textContent==='下案JSONの書式を確認してください。入力は残っています。項目の編集は書式を直してから行ってください。')errors.textContent='';}
      catch(_){rawInvalid=true;errors.textContent='下案JSONの書式を確認してください。入力は残っています。項目の編集は書式を直してから行ってください。';const raw=$('[data-wd-raw]');if(raw){raw.hidden=false;raw.open=true;}}
      if(focused&&editor.isConnected){editor.focus({preventScroll:true});editor.setSelectionRange(start,end);editor.scrollTop=scroll;}
      return;
    }
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
    checkpoint?.controls();
    authoring?.setDisabled(busy||!!token);
    generateButton.disabled = busy || root.dataset.mode === 'waiting' || !!token;
    generateButton.hidden = ['working','review','adopted','adopting'].includes(record?.state)||editableRecord();
    $('[data-wd-adopt]').hidden = record?.state !== 'review'||dirty||!!record?.needs_revalidation;
    name.disabled = overview.disabled = busy || !!token;
    if(timeExplicit){timeExplicit.disabled=busy||!!token;timeFields.hidden=!timeExplicit.checked;timeDays.disabled=timeSlots.disabled=busy||!!token||!timeExplicit.checked;}
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
    if(workspace)workspace.setDisabled(busy||uncertain||!canValidate());
    if(editing)editing.setDisabled(busy||uncertain||rawInvalid||!canValidate());
  }
  async function api(path, body) {
    const response = await fetch('/api/world-drafts' + path, body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) {
      const error = new Error(value.message || '確認できませんでした');
      error.fields = value.field_errors || {}; error.status=response.status; error.known = response.status < 500 && response.status !== 409; throw error;
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
    const conditionText = c => ({reach:()=>`${c.place}に到着する`,acquire:()=>`${c.item}を所持する`,deliver:()=>`${c.item}を所持して${c.place}にいる`,meet:()=>`${c.person}と生存して同じ場所にいる`,befriend:()=>`${c.person}との双方の親しさが${c.threshold}以上になる`,defeat:()=>`${c.person}が戦闘不能または死亡する`,survive:()=>`${c.day}日目の最後の時間帯まで生存する`,discover:()=>`${c.fact}を知る`}[c.type]?.() || '条件を確認してください');
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
    if(!goalEditor)context.append(table('品物と経路',['種類','内容'],[...d.items.map(i=>['品物',i.name+'／所持：'+(i.holders?i.holders.map(h=>h.person+' ×'+h.count).join('、'):i.holder)]),...d.routes.map(r=>['経路',r.from+' → '+r.to])]));
    if(d.facts?.length)context.append(table('調べて得る情報',['名前','内容','調べる場所'],d.facts.map(f=>[f.name,f.description,f.place])));
  }
  function show(value) {
    record=value;uncertain=false;confirm.checked=false;dirty=false;
    if(workspace)root.classList.toggle('is-reviewing',value.state==='review'||editableRecord());
    if(['adopted','cancelled'].includes(value.state)){sessionStorage.removeItem('wb-new-world-draft');carryToken(null);}
    if(value.authoring!==undefined)authoring?.set(value.authoring);
    if(value.name)name.value=value.name;
    if(value.overview)overview.value=value.overview;
    const specifiedTime=value.draft?.settings?.time||value.input_settings?.time;
    if(timeExplicit&&specifiedTime){timeExplicit.checked=true;timeDays.value=specifiedTime.days;timeSlots.value=specifiedTime.slots.join('\n');}
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
    let settings;
    if(timeExplicit?.checked){
      const days=Number(timeDays.value),slots=timeSlots.value.trim().split(/\r?\n/).map(s=>s.trim());
      if(!Number.isInteger(days)||days<1||days>10000||slots.length>100||slots.some(s=>!s||s.length>120)||new Set(slots).size!==slots.length){errors.textContent='期間は1〜10000日の整数、時間帯は重複せず1〜100個で指定してください。';return;}
      settings={version:1,time:{days,slots}};
    }
    token=crypto.randomUUID().replaceAll('-','');sessionStorage.setItem('wb-new-world-draft',token);carryToken(token);const run=++epoch;
    record={state:'generating'};busy=true;errors.textContent='';status.textContent='下案を作成し、概要・目標・達成条件を確認しています。まだ世界には保存していません。';controls();
    try {const r=await api('',{request_id:token,name:name.value,overview:overview.value,...(settings?{settings}:{}),...(getAuthoring()?{authoring:getAuthoring()}:{})});if(run===epoch)show(r);}
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
  editor.addEventListener('input',()=>{markDirty();updateGoalEditor();controls();});
  confirm.addEventListener('change',controls);
  if(timeExplicit)timeExplicit.addEventListener('change',controls);
  $('[data-wd-validate]').addEventListener('click',async()=>{
    if(busy||uncertain||!canValidate())return;
    let draft;try{draft=JSON.parse(editor.value);}catch(_){errors.textContent='JSONの書式を確認してください。入力は残っています。';return;}
    const run=++epoch;busy=true;status.textContent='修正した下案の構造・達成条件と、概要との整合性を確認しています。実モデルでは数分かかる場合があります。';controls();
    try{if(record?.state==='working'){token=crypto.randomUUID().replaceAll('-','');record=await api('/'+token+'/stage',{name:name.value,overview:overview.value,draft,...(getAuthoring()?{authoring:getAuthoring()}:{})});carryToken(token);sessionStorage.setItem('wb-new-world-draft',token);}const r=await api('/'+token+'/validate',{revision:record.edit_revision||record.revision,draft});if(run===epoch)show(r);}catch(error){if(run===epoch){if(record?.state==='working'&&error.known){token=null;carryToken(null);sessionStorage.removeItem('wb-new-world-draft');}failed(error);}}finally{if(run===epoch){busy=false;controls();}}
  });
  $('[data-wd-adopt]').addEventListener('click',async()=>{
    if(busy||uncertain||dirty||record?.needs_revalidation||!confirm.checked||record?.state!=='review')return;
    busy=true;controls();try{show(await api('/'+token+'/adopt',{revision:record.revision}));}catch(error){failed(error);}finally{busy=false;controls();}
  });
  window.addEventListener('beforeunload',e=>{if(token&&!['adopted','cancelled'].includes(record?.state)){e.preventDefault();e.returnValue='';}});
  // A working copy is deliberately independent of AI validation and adoption.
  // Store the exact raw buffer rather than normalizing incomplete user edits.
  if($('[data-wd-save-work]')){
    const save=$('[data-wd-save-work]'),list=$('[data-wd-work-list]'),message=$('[data-wd-work-status]'),check=$('[data-wd-work-check]'),source=$('[data-wd-source-world]');
    let id=null,revision=null,saved=null,pending=false,unknown=false,pendingContent=null,forkSource=null;
    const content=()=>({version:1,name:name.value,overview:overview.value,time:{enabled:!!timeExplicit?.checked,days:String(timeDays?.value??'7'),slots:timeSlots?.value??'朝\n昼\n夕方\n夜'},draft_text:editor.value,source_draft_id:token,source_revision:record?.edit_revision||record?.revision||null,...(getAuthoring()?{authoring:getAuthoring()}:{})});
    const fingerprint=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
    const signature=()=>fingerprint(content());
    const setId=value=>{
      id=value;
      const url=new URL(window.location.href);if(id)url.searchParams.set('workspace',id);else url.searchParams.delete('workspace');
      window.history.replaceState(window.history.state,'',url.pathname+url.search+url.hash);
    };
    const notice=text=>{message.textContent=text;};
    const request=async(path,body)=>{
      const response=await fetch('/api/world-draft-workspaces'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok){const e=new Error(result.message||'下書きを確認できませんでした');e.known=response.status<500;e.conflict=response.status===409;throw e;}return result;
    };
    const guard=()=>{
      if(window.WorldBloomDraftForms?.hasPending()){notice('フォームの変更を先に下書きへ反映してください。');return false;}
      if(editing?.active){notice('項目の編集を「反映」または「取消」で閉じてから、下書きを保存・再開してください。');return false;}
      return true;
    };
    const workControls=()=>{save.disabled=busy||pending||unknown;check.hidden=!unknown;check.disabled=pending;list.disabled=busy||pending;if(source)source.disabled=busy||pending||!!revision;};
    const refresh=async()=>{
      const data=await request('');list.replaceChildren();const first=document.createElement('option');first.value='';first.textContent='選択してください';list.append(first);
      for(const row of data.workspaces){const option=document.createElement('option');option.value=row.workspace_id;option.textContent=(row.name||'名称未定')+(row.unsupported?'':' ／ '+new Date(row.updated_at*1000).toLocaleString());option.disabled=!!row.unsupported;list.append(option);}
      if(id)list.value=id;
    };
    checkpoint={controls:workControls};
    save.addEventListener('click',async()=>{
      if(busy||pending||unknown||!guard())return;
      if(!id)setId(crypto.randomUUID().replaceAll('-',''));
      const captured=content();pendingContent=fingerprint(captured);pending=true;workControls();notice('下書きを保存しています。AIは呼びません。');
      try{const r=await request('/'+id,{revision,request_id:crypto.randomUUID().replaceAll('-',''),content:captured,...(!revision&&forkSource?{source_workspace_id:forkSource}:!revision&&source?.value?{source_world_id:source.value}:{})});revision=r.revision;saved=fingerprint(r.content);notice(signature()===saved?'下書きを保存しました。未検証の内容もそのまま保持しています。':'保存中に加えた変更は未保存です。もう一度保存してください。');}
      catch(e){unknown=!e.known;notice(e.conflict?'別の画面で更新されています。入力は残っています。保存済みの版を再開する前に、現在の内容を控えてください。':unknown?'保存結果が不明です。入力を保持しています。「下書きの保存結果を確認」を押してください。':e.message);}
      finally{pending=false;workControls();}
    });
    const restore=async(r)=>{
      if(r.schema_version!==1||r.content?.version!==1)throw new Error('未対応の下書き形式です。入力は変更しません。');
      const c=r.content;
      // Resolve the execution proposal without generating or validating anything.
      let linked=null;if(c.source_draft_id){try{linked=await api('/'+c.source_draft_id);}catch(e){if(e.status!==404)throw e;}}
      const diverged=linked&&(c.source_revision!==(linked.edit_revision||linked.revision)||fingerprint(c.authoring||null)!==fingerprint(linked.authoring||null));if(diverged)linked=null;
      ++epoch;record=null;token=null;dirty=false;uncertain=false;
      if(linked){token=c.source_draft_id;show(linked);}else{$('[data-wd-review]').hidden=true;root.classList.remove('is-reviewing');}
      name.value=c.name;overview.value=c.overview;authoring?.set(c.authoring||null);timeExplicit.checked=c.time.enabled;timeDays.value=c.time.days;timeSlots.value=c.time.slots;
      editor.value=c.draft_text;
      if(c.draft_text){
        $('[data-wd-review]').hidden=false;
        // Restoring is not approval, even if the last validated proposal matches.
        dirty=true;confirm.checked=false;if(!linked||['adopted','cancelled'].includes(linked.state)){record={state:'working'};token=null;}updateGoalEditor();
        if(canValidate()&&!rawInvalid)root.classList.add('is-reviewing');
        else {const raw=$('[data-wd-raw]');if(raw){raw.hidden=false;raw.open=true;}}
      }
      setId(r.workspace_id);revision=r.revision;saved=fingerprint(c);pendingContent=null;unknown=false;forkSource=null;
      if(source){source.value=r.source_snapshot?.world_id||'';const n=$('[data-wd-source-note]');if(n)n.textContent=r.source_snapshot?`原本を${Object.keys(r.source_snapshot.files).length}ファイル保持しています。原本はAI入力・実行・下案へ自動適用しません。`:'保全した原本はありません。';}
      carryToken(token);if(token)sessionStorage.setItem('wb-new-world-draft',token);else sessionStorage.removeItem('wb-new-world-draft');
      status.textContent='下書きを再開しました。検証・AIへの依頼・世界への採用は実行していません。';notice(diverged?'元の下案の版が一致しません。入力を保持し、確認時は別の下案に分岐します。':'保存した入力を再開しました。採用には明示的な再確認が必要です。');controls();
    };
    const open=async()=>{
      if(busy||pending||unknown||!guard())return;
      const selected=list.value||id;if(!selected)return;
      if(saved!==null&&signature()!==saved&&!window.confirm('現在の未保存入力を置き換えて、選んだ下書きを再開しますか？'))return;
      if(saved===null&&(name.value||overview.value||editor.value)&&!window.confirm('現在の入力を置き換えて、選んだ下書きを再開しますか？'))return;
      pending=true;workControls();try{await restore(await request('/'+selected));}catch(e){notice(e.message+' 入力は変更しません。');}finally{pending=false;workControls();}
    };
    $('[data-wd-work-open]').addEventListener('click',open);
    $('[data-wd-work-fork]').addEventListener('click',()=>{
      if(busy||pending||!guard())return;
      if(unknown&&!window.confirm('先の保存結果は未確定です。現在の入力を別の下書きとして保存しますか？'))return;
      forkSource=revision?id:forkSource;setId(null);revision=null;saved=null;unknown=false;pendingContent=null;workControls();
      save.click();
    });
    $('[data-wd-work-refresh]').addEventListener('click',async()=>{try{await refresh();}catch(e){notice(e.message);}});
    check.addEventListener('click',async()=>{
      if(!id||pending)return;pending=true;workControls();
      try{const r=await request('/'+id);if(fingerprint(r.content)===pendingContent){revision=r.revision;saved=fingerprint(r.content);unknown=false;notice(signature()===saved?'下書きの保存を確認しました。':'送信した版の保存を確認しました。その後の入力は未保存です。');}
        else notice('保存済みの内容と送信した内容が異なります。入力を保持しています。一覧から保存済みの版を確認してください。');}
      catch(e){if(e.known){unknown=false;notice('保存を確認できませんでした。入力は保持しています。'+e.message);}else notice('まだ保存結果を確認できません。自動再送はしません。');}
      finally{pending=false;workControls();}
    });
    window.addEventListener('beforeunload',e=>{if(pending||unknown||signature()!==(saved??fingerprint({version:1,name:'',overview:'',time:{enabled:false,days:'7',slots:'朝\n昼\n夕方\n夜'},draft_text:'',source_draft_id:null,source_revision:null}))){e.preventDefault();e.returnValue='';}});
    const requested=new URLSearchParams(window.location.search).get('workspace');
    if(requested&&/^[a-f0-9]{32}$/.test(requested)){setId(requested);notice('保存した下書きがあります。一覧の「再開する」で読み直せます。');}
    refresh().catch(e=>notice('下書き一覧を取得できません。「一覧を更新」で確認できます。'));
  }
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
