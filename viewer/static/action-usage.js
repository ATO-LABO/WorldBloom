/* A shared world draft; usage never grants actor permission. */
(() => {
  'use strict';
  const root=document.querySelector('[data-action-settings]'),data=document.getElementById('as-data');if(!root||!data)return;
  const $=s=>root.querySelector(s),model=JSON.parse(data.textContent),fields=[...root.querySelectorAll('[data-as-use]')],mechanics=[...root.querySelectorAll('[data-as-mechanic]')],rows=[...root.querySelectorAll('[data-as-row]')];
  const review=$('[data-as-review]'),reset=$('[data-as-reset]'),status=$('[data-as-save-status]'),resolve=$('[data-as-status]');
  const dialog=document.querySelector('[data-as-dialog]'),preview=dialog.querySelector('[data-as-preview]'),save=dialog.querySelector('[data-as-save]'),back=dialog.querySelector('[data-as-back]');
  let constraints=structuredClone(model.constraints||{});
  const enabled=new Set(model.enabled||[]),required=new Set(model.required||[]),original=JSON.stringify(payload());
  let busy=false,dirty=false,blocked=false,pending=null,checked=null,committed=false;
  function payload(){return {revision:model.revision,catalog_version:model.catalog_version,engine_contract:model.engine_contract,
    enabled:[...enabled].sort(),constraints:structuredClone(constraints),mechanics:{version:1,stamina_management:!!mechanics.find(f=>f.dataset.asMechanic==='stamina_management')?.checked,budgeted_purchases:!!mechanics.find(f=>f.dataset.asMechanic==='budgeted_purchases')?.checked},
    world_rules:{vitality:{allow_death:!!mechanics.find(f=>f.dataset.asMechanic==='allow_death')?.checked}}};}
  const typeFields=[...root.querySelectorAll('[data-as-type]')];
  function bundleValues(bundle){return [...bundle.refs.map(ref=>enabled.has(ref)),...(bundle.features||[]).map(key=>!!mechanics.find(f=>f.dataset.asMechanic===key)?.checked)];}
  function refreshTypes(){
    typeFields.forEach(field=>{
      const bundle=(model.type_bundles||[]).find(b=>b.id===field.dataset.asType);if(!bundle)return;
      const values=bundleValues(bundle),all=values.every(Boolean),some=values.some(Boolean);
      field.checked=all;field.indeterminate=some&&!all;field.disabled=busy||blocked||!model.editable;
      field.setAttribute('aria-checked',field.indeterminate?'mixed':String(all));
      const state=root.querySelector('[data-as-type-state="'+bundle.id+'"]');if(state)state.textContent=field.indeterminate?'一部有効':all?'使用中':'未使用';
    });
    const death=mechanics.find(f=>f.dataset.asMechanic==='allow_death');if(death)death.disabled=busy||blocked||!model.editable||!enabled.has('verb:fight');
  }
  typeFields.forEach(field=>field.addEventListener('change',()=>{
    if(busy||blocked||!model.editable)return;
    const bundle=(model.type_bundles||[]).find(b=>b.id===field.dataset.asType);if(!bundle)return;
    const on=field.checked;bundle.refs.forEach(ref=>on?enabled.add(ref):enabled.delete(ref));
    (bundle.features||[]).forEach(key=>{const control=mechanics.find(f=>f.dataset.asMechanic===key);if(control)control.checked=on;});
    checked=null;refresh();message('種類の変更を下書きに反映しました。変更を確認して保存してください。');
  }));
  const detail=$('[data-as-detail-status]');
  function message(text,show=false){if(detail){detail.textContent=text;detail.hidden=!show;}else status.textContent=text;}
  function refresh(){
    const focused=document.activeElement;
    dirty=!committed&&JSON.stringify(payload())!==original;
    fields.forEach(f=>{f.checked=enabled.has(f.dataset.asUse);f.disabled=busy||!model.editable||required.has(f.dataset.asUse);});
    mechanics.forEach(f=>f.disabled=busy||blocked||!model.editable);refreshTypes();
    rows.forEach(row=>{const off=row.dataset.asRef?!enabled.has(row.dataset.asRef):row.dataset.asParent?![...enabled].some(r=>r.startsWith(row.dataset.asParent==='craft'?'recipe:':'response:')):row.dataset.asDisabled==='true';row.dataset.asDisabled=String(off);row.classList.toggle('as-row-disabled',off);const state=row.querySelector('[data-as-row-state]');if(state)state.hidden=!off;});
    const tbody=rows[0]?.parentElement;if(tbody)rows.slice().sort((a,b)=>(a.dataset.asDisabled==='true')-(b.dataset.asDisabled==='true')).forEach(row=>tbody.append(row));
    window.WorldBloomActionGroups?.refresh();
    if(focused&&root.contains(focused)&&!focused.disabled)focused.focus({preventScroll:true});
    review.disabled=busy||blocked||!model.editable||!dirty;reset.disabled=busy||!dirty;
    save.disabled=busy||blocked||!checked;back.disabled=busy;resolve.disabled=busy;
    if(status.classList.contains('ui-settings-status')){
      status.textContent=blocked?'保存状態を確認':busy?'処理中…':dirty?'未保存の変更あり':model.editable?'変更なし':'閲覧専用';
      status.dataset.settingsState=blocked?'blocked':dirty?'dirty':busy?'busy':'saved';
    }
    window.WorldBloomActionPresentation?.sync({busy});
  }
  fields.forEach(f=>f.addEventListener('change',()=>{if(f.checked)enabled.add(f.dataset.asUse);else enabled.delete(f.dataset.asUse);checked=null;refresh();message('未保存の変更があります。行動の種類と行動一覧をまとめて確認・保存します。');}));
  mechanics.filter(f=>!f.hasAttribute('data-as-type')).forEach(f=>f.addEventListener('change',()=>{checked=null;refresh();message('未保存の変更があります。行動の種類と行動一覧をまとめて確認・保存します。');}));
  reset.addEventListener('click',()=>{const initial=JSON.parse(original);constraints=structuredClone(initial.constraints);enabled.clear();initial.enabled.forEach(r=>enabled.add(r));mechanics.forEach(f=>f.checked=f.dataset.asMechanic==='allow_death'?initial.world_rules.vitality.allow_death:initial.mechanics[f.dataset.asMechanic]);checked=null;refresh();message(blocked?'入力を取り消しました。保存状態の確認または再読み込みが必要です。':'未保存の変更はありません。',blocked);});
  async function api(suffix,request){
    const response=await fetch('/api/worlds/'+encodeURIComponent(model.id)+'/action-usage'+suffix,request?{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(request)}:undefined);
    let result;try{result=await response.json();}catch(_){throw Error('サーバーの応答を読み取れませんでした。');}
    if(!response.ok){const error=Error(result.message||'設定を確認してください。');error.status=response.status;throw error;}return result;
  }
  const label=ref=>{const f=fields.find(f=>f.dataset.asUse===ref);return f?.getAttribute('aria-label')?.replace(/をこの世界で使う$/,'')||({stamina_management:'体力管理',budgeted_purchases:'購入時の支払い',allow_death:'戦闘による死亡'})[ref]||ref;};
  function line(text){const p=document.createElement('p');p.textContent=text;preview.append(p);}
  review.addEventListener('click',async()=>{
    if(busy||blocked)return;busy=true;checked=null;refresh();message('変更内容と参照を確認しています。');
    try{const request=payload(),result=await api('/check',request);checked={request,result};preview.replaceChildren();
      if(!result.changes.length)line('使用可否と世界の仕組みは現在の設定を引き継ぎます。');
      result.changes.forEach(change=>{if('constraints' in change){const rule=change.constraints;line(label(change.ref)+'：人物 '+(rule?.actors===null||!rule?'既存の許可を使用':rule.actors.length+'人')+' ／ 時間 '+(rule?.slots?.join('・')||'制限なし'));}else line(label(change.ref||change.setting)+'：'+(change.enabled?'ON':'OFF'));});
      if(result.warnings.length){line('確認事項');result.warnings.forEach(w=>line((w.ref?label(w.ref)+'：':'')+w.message));}
      line('行動の種類と行動一覧を同じ世界設定へ保存します。行動別の人物・時間帯の指定も同じ保存で反映します。人物の材料・知識などの初期値と過去の実行結果は変更しません。');dialog.showModal();message('確認した内容はまだ保存していません。');
    }catch(error){message(error.message,true);if(error.status===409)blocked=true;}
    finally{busy=false;refresh();}
  });
  back.addEventListener('click',()=>{if(!busy)dialog.close();});
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  function applied(result){
    if(result.state==='applied'&&result.matches_current){committed=true;dirty=false;blocked=false;pending=null;message('保存を確認しました。画面を更新します。');location.reload();return true;}
    return false;
  }
  save.addEventListener('click',async()=>{
    if(busy||blocked||!checked)return;
    pending={...checked.request,check_fingerprint:checked.result.check_fingerprint,request_id:crypto.randomUUID()};busy=true;refresh();message('保存しています。');
    try{const result=await api('/save',pending);if(!applied(result)){blocked=true;resolve.hidden=false;message('保存後に別の更新を検出しました。入力を保っています。保存状態を確認してください。',true);}}
    catch(error){blocked=!error.status||error.status>=500||error.status===409;resolve.hidden=!!error.status&&error.status<500&&error.status!==409;message(error.message+(blocked?' 入力を保っています。再保存せず、保存状態を確認してください。':''),true);if(!blocked)pending=null;}
    finally{busy=false;dialog.close();refresh();}
  });
  resolve.addEventListener('click',async()=>{if(busy||!pending)return;busy=true;refresh();try{const result=await api('/status?request_id='+encodeURIComponent(pending.request_id));if(!applied(result))message(result.state==='not_applied'?'この保存は未反映です。入力を控えて再読み込みし、最新の設定と比較してください。':'保存状態を確定できません。入力を保持し、再保存を止めています。',true);}catch(error){message(error.message+' 入力は保持しています。',true);}finally{busy=false;refresh();}});
  addEventListener('beforeunload',event=>{if(!committed&&(dirty||busy||pending||configDirty())){event.preventDefault();event.returnValue='';}});
  root.addEventListener('click',event=>{const link=event.target.closest('a[href]');if(!link)return;if(busy||pending){event.preventDefault();message('保存状態を確認してから移動してください。',true);}else if(dirty&&!confirm('未保存の行動設定の変更を破棄して移動しますか？'))event.preventDefault();});
  // The modal owns a cancellable local draft; only Apply touches the page draft.
  const config=document.querySelector('[data-as-config-dialog]');
  let configRef=null,configOrigin=null,configActors=new Set(),configSlots=new Set(),configLimited=false,configOriginal='';
  const configHelpers=[];
  function clearConfigHelp(){while(configHelpers.length)configHelpers.pop().destroy();}
  function configHelp(host,label,body){const helper=window.WorldBloomHelp?.create(label,body,{theme:'ui-settings-theme',container:config});if(helper){configHelpers.push(helper);host.replaceChildren(helper.button);}}
  addEventListener('pagehide',event=>{if(!event.persisted)clearConfigHelp();});
  const configValue=()=>JSON.stringify({actors:[...configActors].sort(),slots:[...configSlots].sort(),limited:configLimited});
  function configDirty(){return !!config?.open&&configValue()!==configOriginal;}
  if(config){
    const q=s=>config.querySelector(s),search=q('[data-as-person-search]'),only=q('[data-as-person-selected]'),list=q('[data-as-person-list]'),slotList=q('[data-as-time-slots]'),modes=[...config.querySelectorAll('[data-as-time-mode]')];
    const error=q('[data-as-config-error]'),apply=q('[data-as-config-apply]');
    const locked=()=>required.has(configRef)||!model.editable||busy||blocked;
    const same=(a,b)=>JSON.stringify([...a].sort())===JSON.stringify([...b].sort());
    function filterPeople(){
      let visible=0;const query=search.value.trim().toLocaleLowerCase();
      [...list.children].forEach(row=>{const input=row.querySelector('input');row.hidden=!row.textContent.toLocaleLowerCase().includes(query)||(only.checked&&!configActors.has(input.value));if(!row.hidden)visible++;else if(row.contains(document.activeElement))only.focus();});
      q('[data-as-person-count]').textContent='選択中 '+configActors.size+' / '+(model.people||[]).length+'人';
      q('[data-as-person-empty]').hidden=visible!==0;
    }
    function syncModal(){
      const isLocked=locked();
      list.querySelectorAll('input').forEach(f=>{f.checked=configActors.has(f.value);f.disabled=isLocked;});
      modes.forEach(f=>{f.checked=(f.value==='limited')===configLimited;f.disabled=isLocked||!(model.slots||[]).length;});
      q('[data-as-time-selection-hint]').hidden=!configLimited;slotList.hidden=!configLimited;slotList.querySelectorAll('input').forEach(f=>{f.checked=configSlots.has(f.value);f.disabled=isLocked||!configLimited;});

      q('[data-as-config-required]').hidden=!required.has(configRef);
      apply.disabled=isLocked;
      filterPeople();
    }
    function checkbox(value,labelText,parent,onChange){
      const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');input.type='checkbox';input.value=value;text.textContent=labelText;label.append(input,text);parent.append(label);input.addEventListener('change',()=>{onChange(input.checked);error.hidden=true;syncModal();});
    }
    function openConfig(ref,origin){
      if(busy||blocked||!model.actions?.[ref])return;
      configRef=ref;configOrigin=origin;
      const action=model.actions[ref],rule=constraints[ref]||{actors:null,slots:null};
      configActors=new Set(rule.actors??model.default_actors?.[ref]??[]);configSlots=new Set(rule.slots||[]);configLimited=rule.slots!==null;
      configOriginal=configValue();q('[data-as-config-name]').textContent=action.name;search.value='';only.checked=false;error.hidden=true;list.replaceChildren();slotList.replaceChildren();
      (model.people||[]).forEach(person=>checkbox(person.id,person.name,list,on=>on?configActors.add(person.id):configActors.delete(person.id)));
      (model.slots||[]).forEach(slot=>checkbox(slot,slot,slotList,on=>on?configSlots.add(slot):configSlots.delete(slot)));
      clearConfigHelp();
      configHelp(q('[data-as-person-help]'),'対象の人物の説明','この行動を使う人物を選びます。検索や「選択済みのみ」で絞り込んでも、選択は保持されます。知識・材料・場所・役割など、その他の実行条件も必要です。');
      const timed=(model.timed_verbs||[]).includes(action.verb);
      configHelp(q('[data-as-time-help]'),'実行時間の説明','世界の時間設定で定義した時間帯から選びます。'+(timed?'禁止時間帯は作業を停止し、次の許可時間帯から再開します。停止中は他の行動もできません。':''));
      configHelp(q('[data-as-conditions-help]'),'その他の条件・起きる変化',action.conditions+' → '+action.result);
      q('[data-as-config-detail]').href=action.href;
      syncModal();config.querySelector('.as-config-body').scrollTop=0;config.showModal();search.focus();
    }
    root.querySelectorAll('[data-as-config]').forEach(button=>button.addEventListener('click',()=>openConfig(button.dataset.asConfig,button)));
    search.addEventListener('input',filterPeople);only.addEventListener('change',filterPeople);
    modes.forEach(mode=>mode.addEventListener('change',()=>{configLimited=mode.value==='limited';error.hidden=true;syncModal();}));
    function closeConfig(){clearConfigHelp();config.close();configOrigin?.focus({preventScroll:true});}
    q('[data-as-config-cancel]').addEventListener('click',closeConfig);q('[data-as-config-close]').addEventListener('click',closeConfig);
    config.addEventListener('cancel',event=>{event.preventDefault();closeConfig();});
    apply.addEventListener('click',()=>{
      if(locked())return;
      if(configLimited&&!configSlots.size){error.textContent='実行できる時間帯を1つ以上選んでください。';error.hidden=false;error.scrollIntoView({block:'nearest'});modes.find(f=>f.value==='limited')?.focus({preventScroll:true});return;}
      const actors=same(configActors,model.default_actors?.[configRef]||[])?null:[...configActors].sort();
      const rule={actors,slots:configLimited?(model.slots||[]).filter(s=>configSlots.has(s)):null};
      if(rule.actors===null&&rule.slots===null)delete constraints[configRef];else constraints[configRef]=rule;
      checked=null;closeConfig();refresh();message('人物・時間帯の変更を適用しました。上部の「変更を確認」から保存してください。');
    });
    config.addEventListener('click',event=>{const link=event.target.closest('a[href]');if(!link)return;if(busy||pending){event.preventDefault();return;}if((dirty||configDirty())&&!confirm('未保存の変更を破棄して詳細設定へ移動しますか？'))event.preventDefault();});
  }
  refresh();
})();
