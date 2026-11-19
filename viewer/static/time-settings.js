/* Calendar designs are sidecars; the slot engine never reads these drafts. */
'use strict';
class WorldTimeSettings {
  constructor(model) {
    this.model=model;this.data=null;this.settings=null;this.saved='';this.mode='base';
    this.selected=null;this.special=null;this.originalSpecial='';this.busy=false;this.error='';
    this.notice='';this.preview=null;this.blocked=false;this.loadSeq=0;this.host=null;this.loading=false;this.checking=false;
    this.api=`/api/worlds/${encodeURIComponent(model.id)}/time-settings`;
    this.timer=null;this.seq=0;this.overridesOpen=false;
    this.tabs=[['normal','期間と通常日'],['overrides','曜日・休日'],['specials','特別日'],['preview','適用プレビュー'],['events','予定された出来事'],['runtime','既存の時間設定'],['pressure','期限と切迫']];
    this.pressure=new WorldTimePressure(model,()=>this.syncTabs());
    this.pressure.onRender=()=>this.mountPressureHeader();this.pressure.getOtherState=()=>this.getRootBlocked?.()?'別の保存結果を確認してください。':this.basic?.dirty()||this.calendarDirty()?'基本設定の変更を先に保存するか戻してください。':this.busy||this.basic?.busy?'ほかの設定を保存中です。':'';this.pressure.onPreparationState=state=>this.onPreparationState?.(state);this.pressure.getPreparationSnapshot=()=>this.getPreparationSnapshot?.();this.pressure.onOpenPreparation=()=>this.onOpenPreparation?.();
    this.layout=null;this.layoutHelpers=[];this.layoutParent=null;this.parentClasses=[];this.oldTitles=[];this.externalPanels=null;this.scrolls={};this.frameScrolls={};
    this.modern=!!window.WorldBloomTimeBasic&&model.time_basic_version===1;
    this.detailView='';this.retiredNotice='';
    if(this.modern){this.tabs=[['normal','基本設定'],['events','予定された出来事'],['pressure','期限と切迫']];this.basic=window.WorldBloomTimeBasic.create({model,onApplied:result=>this.onApplied?.(result),onState:()=>this.syncTabs(),getBlocked:()=>this.busy||this.blocked||(this.pressure.busy||this.pressure.preparation?.busy)||this.pressure.blocked||this.pressure.dirty()});}
    this.tab='normal';this.lastHash=location.hash;
    document.addEventListener('click',e=>{
      const link=e.target.closest('a[href]');if(!link||(!this.dirty()&&!this.busy&&!(this.pressure.busy||this.pressure.preparation?.busy))||link.target==='_blank'||e.ctrlKey||e.metaKey)return;
      e.preventDefault();e.stopImmediatePropagation();this.guard(()=>{this.leave();location.href=link.href;});
    },true);
    document.addEventListener('change',e=>{
      const picker=e.target.closest('[data-wb="world-picker"]');if(!picker||!this.dirty()&&!this.busy&&!(this.pressure.busy||this.pressure.preparation?.busy))return;
      const value=picker.value;e.stopImmediatePropagation();picker.value=this.model.id;
      this.guard(()=>{this.leave();location.href=`/worlds/${encodeURIComponent(value)}`;});
    },true);
    addEventListener('beforeunload',e=>{if(this.dirty()||this.busy||this.basic?.busy||(this.pressure.busy||this.pressure.preparation?.busy)){e.preventDefault();e.returnValue='';}});

  }
  esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  clone(v){return JSON.parse(JSON.stringify(v));}
  calendarDirty(){return !!this.settings&&(JSON.stringify(this.settings)!==this.saved||
    (this.mode==='special'&&JSON.stringify(this.special)!==this.originalSpecial));}
  dirty(){return !!this.basic?.dirty()||this.pressure.dirty()||this.calendarDirty();}
  guard(action,onContinue=()=>{},calendarOnly=false){
    if(this.busy||this.basic?.busy||(this.pressure.busy||this.pressure.preparation?.busy))return;
    if(!(calendarOnly?this.calendarDirty():this.dirty())){action();return;}
    if(this.confirm?.open)return;
    const dialog=document.createElement('dialog');dialog.className='wp-dialog ts-confirm'+(this.modern?' ui-settings-theme':'');
    dialog.innerHTML=(calendarOnly?'<h2>詳細設定に未保存の下書きがあります</h2><p>下書きの変更を破棄して戻りますか？基本設定と期限・切迫の入力は保持します。</p><div class="ts-actions"><button data-continue>編集を続ける</button><button data-discard>下書きの変更を破棄</button></div>':'<h2>未保存の変更があります</h2><p>変更を破棄して戻りますか？保存済みの内容は保持します。</p><div class="ts-actions"><button data-continue>編集を続ける</button><button data-discard>変更を破棄して戻る</button></div>');
    for(const button of dialog.querySelectorAll('button')){button.type='button';button.className='ui-button '+(button.hasAttribute('data-discard')?'ui-button--confirm':'ui-button--secondary');}document.body.append(dialog);this.confirm=dialog;
    const close=()=>{dialog.close();dialog.remove();this.confirm=null;};
    dialog.querySelector('[data-continue]').onclick=()=>{close();onContinue();};
    dialog.querySelector('[data-discard]').onclick=()=>{close();if(calendarOnly)this.restoreCalendar();else this.restore();action();};
    dialog.addEventListener('cancel',e=>{e.preventDefault();close();onContinue();});dialog.showModal();
  }
  restore(){this.basic?.restore();this.pressure.restore();this.restoreCalendar();}
  restoreCalendar(){if(this.saved)this.settings=JSON.parse(this.saved);this.discardEditor();}
  discardEditor(){this.special=null;this.originalSpecial='';this.selected=null;}
  leave(preserveHash=false){this.basic?.unmount();this.pressure.unmount();this.releaseFrame(true);if(!preserveHash&&history.state?.wbTimeSpecial)history.replaceState({},'',`${location.pathname}${location.search}`);this.mode='base';this.checking=false;this.discardEditor();this.host=null;}
  refreshWorld(model){this.loadSeq++;this.loading=false;this.basic?.refreshWorld(model);this.pressure.refreshWorld(model);this.model=model;this.data=null;this.preview=null;if(!this.dirty())this.settings=null;}
  mount(host){
    if(this.modern&&this.data?.world_revision&&this.data.world_revision!==this.model.revision)this.refreshWorld(this.model);
    if(this.host!==host){this.releaseFrame(true);this.basic?.unmount();this.pressure.unmount();this.host=host;this.externalPanels=null;this.tab=this.tabFromHash();this.lastHash=location.hash;}
    if(!this.modern&&!this.data&&!this.loading)this.load();this.render();
  }
  retiredTab(key){return ['advanced','overrides','specials','preview'].includes(key);}
  retiredMessage(){return '旧「詳細設定」のリンクです。設計用下書き・設計版は保持し、この画面の編集から外しました。保存済みのCalendar契約と実行履歴は変更していません。通常方式の期間・曜日・休日は基本設定に集約しています。既存Calendarは適用中契約の読取表示を確認できます。';}
  tabFromHash(){const key=new URLSearchParams(location.hash.slice(1)).get('tab');if(this.modern){if(this.retiredTab(key)||location.hash==='#time-special'){this.retiredNotice=this.retiredMessage();return 'normal';}if(key==='runtime')return 'normal';}const tab=key;return this.tabs.some(([id])=>id===tab)?tab:!this.modern&&location.hash==='#time-special'?'specials':'normal';}
  rememberTab(push=false){const hash=this.mode==='special'?'#time-special':'#'+new URLSearchParams({screen:'time',tab:this.tab}),state={...history.state};if(this.mode!=='special')delete state.wbTimeSpecial;history[push?'pushState':'replaceState'](state,'',hash);this.lastHash=hash;}
  restoreTabFromHash(){
    if(this.busy||this.basic?.busy||(this.pressure.busy||this.pressure.preparation?.busy)){history.replaceState(history.state||{},'',this.lastHash);return true;}
    const hash=new URLSearchParams(location.hash.slice(1));
    if(hash.get('screen')!=='time'&&!['#time','#time-special'].includes(location.hash))return false;
    if(!this.modern&&location.hash==='#time-special'&&this.mode==='special')return true;
    this.selectTab(this.tabFromHash(),false);return true;
  }
  selectTab(tab,push=true){
    if(this.busy||this.basic?.busy||(this.pressure.busy||this.pressure.preparation?.busy)){history.replaceState(history.state||{},'',this.lastHash);return;}
    let detail='';if(this.modern&&this.retiredTab(tab)){this.retiredNotice=this.retiredMessage();tab='normal';detail='retired';}else if(this.modern&&tab==='runtime')tab='normal';
    if(!this.tabs.some(([key])=>key===tab)||push&&tab===this.tab&&!detail)return;
    const activate=()=>{this.tab=tab;if(detail&&detail!=='retired'){this.detailView=detail;if(detail==='overrides')this.overridesOpen=true;}if(this.mode==='special'){this.mode='base';this.discardEditor();}if(this.modern)this.render();else{if(tab==='preview'&&this.checking)this.host?.querySelector('.ts-diff')?.replaceChildren(document.createTextNode(this.diff()));this.syncTabs();this.mountPressure();}this.rememberTab(push);this.host?.querySelector(`[data-ts-tab="${tab}"]`)?.focus({preventScroll:true});};
    if(this.mode==='special')this.guard(activate,()=>history.replaceState(history.state||{},'',this.lastHash),this.modern);else activate();
  }
  tabsHtml(){return `<div class="ui-tabs wp-tabs" role="tablist" aria-label="時間と出来事の設定">${this.tabs.map(([key,label])=>`<button type="button" id="ts-tab-${key}" data-ts-tab="${key}" role="tab" aria-controls="ts-panel-${key}" aria-selected="${this.tab===key}" aria-pressed="${this.tab===key}" tabindex="${this.tab===key?0:-1}">${label}</button>`).join('')}</div>`;}
  panel(key,html){return `<section class="wp-section" id="ts-panel-${key}" data-ts-panel="${key}" role="tabpanel" aria-labelledby="ts-tab-${key}" ${this.tab===key?'':'hidden'}>${html}</section>`;}
  syncTabs(){
    this.host?.querySelectorAll('[data-ts-tab]').forEach(b=>{const active=b.dataset.tsTab===this.tab;b.setAttribute('aria-selected',String(active));b.setAttribute('aria-pressed',String(active));b.tabIndex=active?0:-1;b.disabled=this.busy||this.basic?.busy||(this.pressure.busy||this.pressure.preparation?.busy);});
    this.host?.querySelectorAll('[data-ts-calendar-actions]').forEach(el=>el.hidden=this.modern?this.tab!=='advanced':this.tab==='pressure');
    const status=this.host?.querySelector('[data-ts-draft-status]');if(status)status.textContent=this.busy?'下書き保存中…':this.blocked?'確認が必要な下書きがあります。':this.calendarDirty()?'未保存の下書きがあります。':'下書きに未保存の変更はありません。';
    this.host?.parentElement.querySelectorAll('[data-ts-panel]').forEach(panel=>{panel.hidden=panel.dataset.tsPanel!==this.tab;});
    this.syncFrameHeader();
  }
  mountPressure(){if(this.modern){if(this.tab==='normal')this.basic?.mount(this.host?.querySelector('[data-time-basic-host]'),{headerHost:this.layout?.header});else this.basic?.unmount();}if(this.tab==='pressure')this.pressure.mount(this.host?.querySelector('[data-tp-root]'),{headerHost:this.modern?this.layout?.header:null});else this.pressure.unmount();if(this.layout&&!this.layout.geometry)this.layout.geometry=window.WorldBloomSettingsFrame?.mount(this.layout.frame,{header:this.layout.header,tabs:this.layout.tabs,body:this.layout.body});this.layout?.geometry?.refresh();}
  node(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
  releaseFrame(full=false){
    if(this.layout){this.scrolls[this.layout.tab]=this.layout.body.scrollTop||0;this.frameScrolls[this.layout.tab]=this.layout.frame.scrollTop||0;this.layout.geometry?.disconnect();this.layout=null;}
    for(const helper of this.layoutHelpers)helper.destroy();this.layoutHelpers=[];
    if(full){for(const old of this.oldTitles)old.node.hidden=old.hidden;this.oldTitles=[];if(this.layoutParent){for(const name of this.parentClasses)this.layoutParent.classList.remove(name);}this.parentClasses=[];this.layoutParent=null;this.host?.classList.remove('ui-settings-host');this.host?.removeAttribute('data-time-modern');this.externalPanels=null;}
  }
  prepareFrame(){
    if(!this.modern||!this.host?.parentElement)return;
    this.releaseFrame();
    if(!this.externalPanels)this.externalPanels=Array.from(this.host.parentElement.children).filter(el=>['events','runtime'].includes(el.dataset.tsPanel));
    if(!this.layoutParent){this.layoutParent=this.host.parentElement;for(const name of ['ui-settings-host','ui-settings-theme','time-settings-page'])if(!this.layoutParent.classList.contains(name)){this.layoutParent.classList.add(name);this.parentClasses.push(name);}for(const el of Array.from(this.layoutParent.children))if(el!==this.host&&(el.classList.contains('wp-title')||el.classList.contains('wp-subtitle')||el.hasAttribute('data-settings-save-note'))){this.oldTitles.push({node:el,hidden:el.hidden});el.hidden=true;}}
  }
  frameHelp(host,label,text){const helper=window.WorldBloomHelp?.create(label,text,{theme:'ui-settings-theme'});if(helper){this.layoutHelpers.push(helper);host.append(helper.button);}}
  applyFrame(){
    if(!this.modern||!this.host?.isConnected)return;
    const tabs=this.host.querySelector('[role="tablist"]');if(!tabs)return;
    const content=Array.from(this.host.children).filter(el=>el!==tabs),frame=this.node('section','ui-settings-frame ui-settings-theme time-settings-frame'),header=this.node('header','ui-settings-header'),body=this.node('div','ui-settings-body');
    frame.setAttribute('data-time-settings-frame','');body.setAttribute('data-time-settings-body','');tabs.classList.add('ui-settings-tabs');
    for(const el of content)body.append(el);for(const panel of this.externalPanels||[])body.append(panel);
    const advanced=body.querySelector('#ts-panel-advanced');for(const el of Array.from(body.children))if(advanced&&el!==advanced&&!el.hasAttribute('data-ts-panel'))advanced.append(el);
    for(const panel of body.querySelectorAll('[data-ts-panel]'))panel.classList.add('time-settings-panel');
    for(const card of Array.from(advanced?.children||[]))if(card.tagName==='DETAILS')card.classList.add('ui-settings-card');
    for(const table of body.querySelectorAll('[data-ts-panel="events"] table'))table.classList.add('ui-settings-table');
    frame.append(header,tabs,body);this.host.replaceChildren(frame);this.host.classList.add('ui-settings-host');this.host.setAttribute('data-time-modern','');
    this.layout={frame,header,tabs,body,tab:this.tab,geometry:null};
    if(this.tab!=='normal'){
      const title=this.node('div','ui-settings-title'),name=this.node('div','ui-settings-name'),heading=this.node('h1','','時間と出来事'),actions=this.node('div','ui-settings-actions'),status=this.node('p','ui-settings-status');heading.setAttribute('tabindex','-1');name.append(heading);title.append(name);status.setAttribute('role','status');actions.append(status);header.append(title,actions);
      this.layout.name=name;this.layout.actions=actions;this.layout.status=status;
      if(this.tab==='advanced'){
        status.remove();const draftStatus=advanced?.querySelector('[data-ts-draft-status]');if(draftStatus){draftStatus.className='ui-settings-status';actions.append(draftStatus);this.layout.status=draftStatus;}
        for(const key of ['cancel','save']){const button=advanced?.querySelector('[data-ts-'+key+']');if(button){button.type='button';button.className='ui-button '+(key==='save'?'ui-button--confirm':'ui-button--secondary');if(key==='cancel')button.textContent='変更を戻す';actions.append(button);}}
        const error=advanced?.querySelector('[data-ts-message]');if(error){error.classList.add('ui-settings-error');error.setAttribute('role','alert');header.append(error);}
        const oldHeading=advanced?.querySelector('h2');if(oldHeading?.textContent==='詳細設定')oldHeading.remove();
        this.frameHelp(name,'詳細設定の説明','このタブは設計用下書きです。下書き保存は通常日・曜日休日・特別日をまとめて保存し、基本設定や期限と切迫の入力は変えません。実行版への確定は未対応です。');
      }else if(this.tab==='events'){
        this.frameHelp(name,'予定された出来事の説明','出来事はそれぞれの編集画面で保存します。基本設定・期限と切迫とは保存単位が別です。時間帯を改名・削除するときは、出来事の参照を確認してください。');
        status.textContent=this.model.editable?'項目ごとに保存':'閲覧専用';status.hidden=!!this.model.editable;
      }
    }
    body.scrollTop=this.scrolls[this.tab]||0;frame.scrollTop=this.frameScrolls[this.tab]||0;this.syncFrameHeader();
  }
  syncFrameHeader(){
    if(!this.layout)return;const {status}=this.layout;
    if(status){const busy=this.tab==='pressure'?(this.pressure.busy||this.pressure.preparation?.busy):this.busy,blocked=this.tab==='pressure'?this.pressure.blocked:this.blocked,dirty=this.tab==='pressure'?this.pressure.dirty():this.calendarDirty();status.dataset.settingsState=busy?'busy':blocked?'blocked':dirty?'dirty':'saved';if(this.tab==='pressure')status.textContent=busy?'保存中…':blocked?'確認が必要です':dirty?'未保存の変更あり':'保存済みの根拠';}
    for(const helper of this.layoutHelpers){helper.button.disabled=this.busy||this.basic?.busy||(this.pressure.busy||this.pressure.preparation?.busy);if(helper.button.disabled)helper.close();}
    this.layout.geometry?.refresh();
  }
  mountPressureHeader(){
    if(!this.modern||this.tab!=='pressure'||!this.layout||!this.pressure.host)return;
    if(this.pressure.preparation){if(this.layout.status)this.layout.status.hidden=true;this.syncFrameHeader();return;}
    const {header,name,actions}=this.layout,host=this.pressure.host,form=host.querySelector('[data-tp-form]');if(!form)return;
    form.id='time-pressure-form-'+encodeURIComponent(this.model.id);header.setAttribute('data-tp-header','');header.onclick=e=>this.pressure.host?.onclick?.(e);
    const oldHeading=form.querySelector('h2');if(oldHeading?.textContent==='期限と切迫')oldHeading.remove();
    for(const card of form.querySelectorAll('fieldset'))card.classList.add('ui-settings-card');
    const status=host.querySelector('[data-tp-status]');if(status){this.layout.status?.remove();status.className='ui-settings-status';actions.replaceChildren(status);this.layout.status=status;}else actions.replaceChildren();
    for(const key of ['reset','save']){const button=host.querySelector('[data-tp-'+key+']');if(button){button.setAttribute('form',form.id);actions.append(button);}}
    const error=host.querySelector('[data-tp-error]');if(error){header.querySelector('[data-tp-error]')?.remove();error.classList.add('ui-settings-error');header.append(error);}
    if(this.pressure.help){for(const old of Array.from(name.children))if(old.hasAttribute('data-ui-help'))old.remove();name.append(this.pressure.help.button);}
    this.syncFrameHeader();
  }
  validDraft(result,expected){
    const settings=result?.settings;
    if(typeof result?.revision!=='string'||!settings?.period||!settings.normal||!Array.isArray(settings.normal.bands)||!settings.normal.weekdays||!Array.isArray(settings.normal.holidays)||!Array.isArray(settings.specials))return false;
    const ordered=v=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v;
    return expected===undefined||JSON.stringify(ordered(settings))===JSON.stringify(ordered(expected));
  }
  async request(action,settings){
    const writing=!!settings&&action!=='validate';let response,result;
    try{response=await fetch(this.api+(action?'/'+action:''),{method:settings?'POST':'GET',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},...(settings?{body:JSON.stringify({revision:this.data.revision,settings})}:{})});result=await response.json();}
    catch{throw Object.assign(new Error('通信結果を確認できませんでした。'),{blocked:writing});}
    if(!response.ok)throw Object.assign(new Error(result?.message||'保存できませんでした。'),{blocked:response.status===409||response.status>=500||response.status===408});
    if(!result||typeof result!=='object')throw Object.assign(new Error('応答の形式を確認できませんでした。'),{blocked:writing});
    return result;
  }
  async load(){if(this.modern)return;const generation=++this.loadSeq;this.loading=true;this.error='';try{
    const result=await this.request('',null);if(generation!==this.loadSeq)return;if(!this.validDraft(result))throw new Error('設定の応答が不正です。');this.blocked=false;
    this.data=result;this.settings=this.clone(result.settings);this.saved=JSON.stringify(result.settings);this.preview=result;
  }catch(e){if(generation!==this.loadSeq)return;this.error=e.message||'設定を読み込めませんでした。';}finally{if(generation===this.loadSeq){this.loading=false;this.render();}}}
  band(){return {id:'day',name:'通常',start:'09:00',end:'17:00',overnight:false,interval_minutes:60,event_id:''};}
  field(name,label,value,type='text',extra=''){return `<label>${this.esc(label)}<input data-ts-field="${name}" type="${type}" value="${this.esc(value)}" ${extra} ${this.model.editable?'':'disabled'}></label>`;}
  bandsForm(bands,prefix){return bands.map((band,i)=>`<fieldset><legend>時間帯 ${i+1}</legend>${this.field(`${prefix}.${i}.id`,'対象時間帯 ID',band.id)}${this.field(`${prefix}.${i}.name`,'時間帯の名前',band.name)}<div class="ts-pair">${this.field(`${prefix}.${i}.start`,'開始',band.start,'time')}${this.field(`${prefix}.${i}.end`,'終了',band.end,'time')}</div>${this.field(`${prefix}.${i}.interval_minutes`,'判断間隔（分）',band.interval_minutes,'number','step="any"')}<label><input type="checkbox" data-ts-field="${prefix}.${i}.overnight" ${band.overnight?'checked':''}> 夜を跨ぐ</label><label>既存の出来事から開始時刻を参照<select data-ts-field="${prefix}.${i}.event_id" ${this.model.editable?'':'disabled'}><option value="">参照しない</option>${band.event_id&&!(this.data.scheduled_events||[]).some(ev=>ev.id===band.event_id)?`<option value="${this.esc(band.event_id)}" selected>${this.esc(band.event_id)}（参照先がありません）</option>`:''}${(this.data.scheduled_events||[]).map(ev=>`<option value="${this.esc(ev.id)}" ${ev.id===band.event_id?'selected':''}>${this.esc(ev.label||ev.id)} · ${this.esc(ev.slot||'日の初め')}</option>`).join('')}</select></label><button type="button" data-ts-remove-band="${prefix}:${i}" ${this.model.editable?'':'disabled'}>この時間帯を削除</button></fieldset>`).join('')+`<button data-ts-add-band="${prefix}" ${this.model.editable?'':'disabled'}>時間帯を追加</button>`;}
  selectorText(s){const x=s.selector||{};return ({first:'初日',last:'最終日'}[x.kind]||x.start||'日付未設定')+(x.kind==='range'?` ～ ${x.end||'未設定'}`:'')+(x.offset?` ${x.offset}日後`:'');}
  list(editing=false){const rows=this.settings.specials;
    if(!rows.length)return `<p class="ts-empty">特別日は未登録です。通常日の設定を使います。</p>`;
    return `<ul class="ts-special-list">${rows.map((s,i)=>`<li ${s.id===this.notice?'class="is-saved"':''}><button data-ts-edit="${i}" ${this.model.editable?'':'disabled'} aria-pressed="${this.selected===i}"><strong>${this.esc(s.name||'名称未入力')}</strong><span>${this.esc(this.selectorText(s))}</span><small>${s.enabled?'有効':'無効'} · 優先順位 ${this.esc(s.priority)}</small>${this.sourcesConflict(s.id)?'<small class="ts-error">同順位競合 · 適用未確定</small>':''}</button>${editing?'':`<button data-ts-delete="${i}" ${this.model.editable?'':'disabled'}>削除</button>`}</li>`).join('')}</ul>`+(rows.every(s=>!s.enabled)?'<p>登録済みの特別日はすべて無効です。通常設定のみ適用します。</p>':'');}
  sourcesConflict(id){return (this.preview?.validation?.errors||[]).some(e=>e.code==='conflict'&&(e.sources||[]).includes(id));}
  previewHtml(){const value=this.preview;if(!value)return '<p>入力を検証しています…</p>';
    const validation=value.validation||{},preview=value.preview||{};
    return `<p class="ts-status">${validation.valid?'入力検証: 有効':'入力検証: エラーあり'} · ${preview.status==='unresolved'?'適用日付・時刻は未確定':preview.status==='invalid'?'適用未確定':'設計用プレビュー'}</p>`+
      (validation.errors_truncated?'<p>エラーは先頭200件まで表示しています。</p>':'')+(validation.errors||[]).map(e=>`<p class="ts-error">${this.esc(this.fieldLabel(e.field))}: ${this.esc(e.message)}${e.sources?.length?`（${e.sources.map(id=>this.esc(this.settings.specials.find(s=>s.id===id)?.name||id)).join(' / ')}）`:''}</p>`).join('')+
      [...new Set([...(validation.unresolved||[]),...(preview.unresolved||[])].map(e=>typeof e==='string'?e:e.message||JSON.stringify(e)))].map(e=>`<p>${this.esc(e)}</p>`).join('')+
      `<label>日付別プレビュー<select data-ts-preview-date>${(preview.days||[]).map(d=>`<option value="${this.esc(d.date)}" ${d.date===this.previewDate?'selected':''}>${this.esc(d.date)}</option>`).join('')}</select></label>`+this.dayHtml()+
      (preview.truncated?'<p>プレビューは先頭366日までです。</p>':'')+(preview.segments_truncated?'<p>区間表示は合計10,000区間までです。設定検証は全期間に対して行います。</p>':'')+'<p class="wp-muted">移動などの行動時間は変えません。10分の移動は10分です。この表示は実行エンジンに適用されません。</p>';
  }
  dayHtml(){const days=this.preview?.preview?.days||[],day=days.find(d=>d.date===this.previewDate)||days[0];if(!day)return '<p>開始日と最終日を指定すると日付別に確認できます。今日の日付で補完しません。</p>';
    return `<div data-ts-day><h3>${this.esc(day.date)}</h3>${(day.conflicts||[]).map(c=>`<p class="ts-error">適用未確定: ${this.esc(c.message||`時間帯 ${c.band_id}・優先順位 ${c.priority} : ${(c.sources||[]).map(id=>this.settings.specials.find(s=>s.id===id)?.name||id).join(' / ')}`)}</p>`).join('')}${(day.bands||[]).map(b=>`<details open><summary>${this.esc(b.name||b.id)} · ${this.esc(b.interval_minutes)}分</summary><p>適用元: ${this.esc((b.source_ids||[]).join(' / '))}${b.event_reference?` · 出来事ID ${this.esc(b.event_reference.id)} (${this.esc(b.event_reference.slot)})`:''}</p><p>${(b.segments||[]).map(s=>`${s.start_day_offset?'翌日 ':''}${this.esc(s.start)}–${s.end_day_offset?'翌日 ':''}${this.esc(s.end)} (${s.duration_minutes}分${s.short?'・最後の短い区間':''})`).join('、')||'時刻未確定'}</p>${b.remainder_minutes?`<p>最後の短い区間: ${b.remainder_minutes}分</p>`:''}</details>`).join('')}</div>`;
  }
  currentSettings(){const settings=this.clone(this.settings);if(this.mode==='special'&&this.special){if(this.selected===null)settings.specials.push(this.clone(this.special));else settings.specials[this.selected]=this.clone(this.special);}return settings;}
  async validate(){if(this.modern||!this.data)return;const token=++this.seq;try{const result=await this.request('validate',this.currentSettings());if(token===this.seq){this.preview=result;this.updatePreview();}}catch(e){if(token===this.seq){this.error=e.message;this.updatePreview();}}}
  updatePreview(){this.host?.querySelectorAll('[data-ts-preview]').forEach(e=>{e.innerHTML=this.previewHtml();});this.host?.querySelector('[data-ts-message]')?.replaceChildren(document.createTextNode(this.error));const target=this.host?.querySelector('[data-ts-execution-check]');if(target&&this.preview?.execution_check){const report=this.preview.execution_check;target.textContent='実行版の確認：'+(report.calendar_compiles?'暦のコンパイルは有効。':'暦・予定の参照に確認事項があります。')+' '+[...(report.errors||[]),...(report.blockers||[])].join(' / ');}this.host?.querySelectorAll('[data-ts-edit]').forEach(b=>{const id=this.settings.specials[Number(b.dataset.tsEdit)]?.id;const old=b.querySelector('.ts-error');old?.remove();if(this.sourcesConflict(id))b.insertAdjacentHTML('beforeend','<small class="ts-error">同順位競合 · 適用未確定</small>');});}
  schedulePreview(){clearTimeout(this.timer);this.timer=setTimeout(()=>this.validate(),180);}
  render(){
    if(this.modern){
      if(!this.host?.isConnected)return;this.prepareFrame();
      const notice=this.retiredNotice?'<p class="ui-notice" role="status" data-ts-retired-notice>'+this.esc(this.retiredNotice)+'</p>':'';
      this.host.innerHTML=this.tabsHtml()+this.panel('normal',notice+'<div data-time-basic-host></div>')+this.panel('pressure','<div data-tp-root></div>');
      this.applyFrame();this.bind();this.syncTabs();this.mountPressure();return;
    }if(!this.host?.isConnected)return;
const overrides=this.host.querySelector('[data-ts-overrides]');if(overrides)this.overridesOpen=overrides.open;this.prepareFrame();
    if(!this.data){this.host.innerHTML=this.tabsHtml()+(this.modern?this.panel('normal','<div data-time-basic-host></div>')+this.panel('advanced','<p role="status">詳細設定の下書きを読み込んでいます…</p>'):'')+this.panel('pressure','<div data-tp-root></div>')+`<p data-ts-calendar-actions>${this.loading?'時間設定の下書きを読み込んでいます…':this.esc(this.error)}</p>${this.loading?'':'<button data-ts-retry>再試行</button>'}`;this.applyFrame();this.bind();this.syncTabs();this.mountPressure();return;}
    const disabled=this.busy||this.blocked||!this.model.editable?'disabled':'';
    const banner='<p data-ts-calendar-actions class="ts-banner">設計用下書き・適用プレビューです。Calendarエンジンはありますが、この設計用下書きから実行版への確定は未対応です。下書き保存や設計版の記録では、既存の世界・実行・ログ・再開設定を変更しません。</p>';
    const message=`<p data-ts-message role="status" class="ts-error">${this.esc(this.error)}</p>`;
    const actions=`<div class="ts-actions"><button data-ts-save ${disabled}>${this.busy?'保存中…':'下書き保存'}</button><button data-ts-cancel ${disabled}>キャンセル</button></div>`;
    let special=`<h2>特別日一覧</h2>${this.list()}<button data-ts-add ${disabled}>特別日を追加</button>`;
    if(this.mode==='special'){
      const s=this.special;special=`<div class="ts-editor"><section><h2>特別日一覧</h2>${this.list(true)}<button data-ts-add ${disabled}>特別日を追加</button></section><section><h2>選択中の特別日を編集</h2>${this.field('special.name','名前',s.name)}<label><input data-ts-field="special.enabled" type="checkbox" ${s.enabled?'checked':''}> 有効</label><label>対象日<select data-ts-field="special.selector.kind">${[['date','単日'],['range','日付範囲'],['first','初日から'],['last','最終日から']].map(([v,l])=>`<option value="${v}" ${s.selector.kind===v?'selected':''}>${l}</option>`).join('')}</select></label><div data-ts-selector>${this.selectorForm()}</div>${this.field('special.priority','優先順位（大きい値を優先）',s.priority,'number')}${this.bandsForm(s.bands,'special.bands')}</section><aside><h2>適用結果プレビュー</h2><div data-ts-preview>${this.previewHtml()}</div></aside></div>`;
    }
    const preview=this.checking?`<h2>設定を確認</h2><p>保存済み下書きとの変更点</p><pre class="ts-diff">${this.esc(this.diff())}</pre><div data-ts-preview>${this.previewHtml()}</div><button data-ts-archive ${disabled}>設計版を記録（実行には使えません）</button><button disabled>実行用の新しい版として確定（未対応）</button><button data-ts-back>プレビューに戻る</button><h3>記録済みの設計版</h3>${this.versionsHtml()}`:`<h2>日付別の適用プレビュー</h2><div data-ts-preview>${this.previewHtml()}</div><button data-ts-check ${disabled}>設定を確認</button>`;

    this.host.innerHTML=this.tabsHtml()+banner+
      this.panel('normal',`<div data-ts-normal><h2>期間と通常日の時間設定</h2><div class="ts-pair">${this.field('period.start_date','開始日（未確定なら空欄）',this.settings.period.start_date,'date')}${this.field('period.end_date','最終日（未確定なら空欄）',this.settings.period.end_date,'date')}</div>${this.bandsForm(this.settings.normal.bands,'normal.bands')}</div>`)+
      this.panel('overrides',`<h2>曜日・休日差の設定</h2>${this.calendarOverrides()}`)+
      this.panel('specials',`<div data-ts-specials>${special}</div>`)+this.panel('preview',preview)+this.panel('pressure','<div data-tp-root></div>')+
      '<div data-ts-calendar-actions>'+message+(this.notice?'<p role="status">下書きを保存しました。</p>':'')+
      '<p class="ui-hint">下書き保存は、期間と通常日・曜日と休日・特別日の設定をまとめて保存します。予定された出来事と既存の時間設定は、それぞれの編集画面で保存します。</p>'+actions+'</div>';

    if(this.busy||!this.model.editable)this.host.querySelectorAll('input,textarea,select,button').forEach(el=>el.disabled=true);
    this.applyFrame();this.bind();this.syncTabs();this.mountPressure();
  }

  calendarOverrides(){
    const names=['月曜','火曜','水曜','木曜','金曜','土曜','日曜'];
    return `<details data-ts-overrides ${this.overridesOpen?'open':''}><summary>曜日・休日差の設定</summary><p class="wp-muted">同じ対象時間帯 ID の設定を通常日より優先します。特別日はその後に優先順位で適用します。</p>${Object.entries(this.settings.normal.weekdays).map(([day,bands])=>`<section><h3>${names[Number(day)]}の時間設定</h3>${this.bandsForm(bands,'normal.weekdays.'+day)}<button data-ts-remove-weekday="${day}">曜日の差を削除</button></section>`).join('')}<label>差を追加する曜日<select data-ts-weekday>${names.map((name,i)=>`<option value="${i}">${name}</option>`).join('')}</select></label><button data-ts-add-weekday>曜日の差を追加</button><h3>休日</h3>${this.settings.normal.holidays.map((holiday,i)=>`<section>${this.field('normal.holidays.'+i+'.date','休日の日付',holiday.date,'date')}${this.bandsForm(holiday.bands,'normal.holidays.'+i+'.bands')}<button data-ts-remove-holiday="${i}">休日の差を削除</button></section>`).join('')}<button data-ts-add-holiday>休日の差を追加</button></details>`;
  }
  selectorForm(){const s=this.special.selector;return ['first','last'].includes(s.kind)?this.field('special.selector.offset','相対日数（負数は前）',s.offset??0,'number'):this.field('special.selector.start','対象日／開始日',s.start??'','date')+(s.kind==='range'?this.field('special.selector.end','終了日',s.end??'','date'):'');}
  fieldLabel(field){
    const match=/^specials\.(\d+)/.exec(field);
    if(match)return '特別日 '+(this.settings.specials[Number(match[1])]?.name||'編集中');
    if(field.startsWith('period'))return '期間';if(field.startsWith('normal.weekdays'))return '曜日の時間設定';if(field.startsWith('normal.holidays'))return '休日の時間設定';if(field.startsWith('normal'))return '通常日の時間設定';return '時間設定';
  }
  diff(){
    if(JSON.stringify(this.settings)===this.saved)return '保存済み下書きからの変更なし';
    const describe=s=>`期間: ${s.period.start_date||'未確定'} ～ ${s.period.end_date||'未確定'}\n通常日: ${s.normal.bands.map(b=>`${b.name} ${b.start}–${b.end} / ${b.interval_minutes}分`).join('、')}\n曜日差: ${Object.keys(s.normal.weekdays).length}件 / 休日差: ${s.normal.holidays.length}件\n特別日: ${s.specials.map(row=>`${row.name||'名称未入力'} (${this.selectorText(row)}・${row.enabled?'有効':'無効'}・優先${row.priority}・${row.bands.map(b=>b.interval_minutes+'分').join('/')})`).join('、')||'未登録'}`;
    return '保存済み下書き\n'+describe(JSON.parse(this.saved))+'\n\n現在の入力\n'+describe(this.settings);
  }
  versionsHtml(){return (this.data.versions||[]).map(v=>`<p>${this.esc(typeof v==='string'?v:v.id)} · 設計用（実行不可）</p>`).join('')||'<p>まだ設計版を記録していません。</p>';}
  open(index=null){if(this.modern){this.selectTab('advanced');return;}this.guard(()=>{
    this.mode='special';this.tab=this.modern?'advanced':'specials';this.detailView='specials';this.checking=false;this.selected=index;
    this.special=index===null?{id:'special-'+crypto.randomUUID(),name:'',enabled:true,priority:10,selector:{kind:'date',start:'',end:'',offset:0},bands:[this.clone(this.settings.normal.bands[0]||this.band())]}:this.clone(this.settings.specials[index]);
    this.originalSpecial=JSON.stringify(this.special);this.error='';
    if(!history.state?.wbTimeSpecial)history.pushState({wbTimeSpecial:true},'',`${location.pathname}${location.search}#time-special`);
    this.lastHash=location.hash;this.render();this.validate();
  },()=>{},this.modern);}
  async save(action=''){
    if(this.modern)return;
    if(this.busy||this.blocked||!this.model.editable||this.basic?.busy){this.render();return;}const settings=this.currentSettings();
    this.busy=true;clearTimeout(this.timer);this.seq++;this.error='';this.notice='';this.render();
    try{if(action==='archive_design'){const draft=await this.request('',settings);if(!this.validDraft(draft,settings))throw Object.assign(new Error('下書きの保存結果が不正です。'),{blocked:true});this.data=draft;this.saved=JSON.stringify(draft.settings);}const result=await this.request(action,settings);if(!this.validDraft(result,settings))throw Object.assign(new Error('保存結果が不正です。'),{blocked:true});
      const id=this.special?.id||'saved';this.data=result;this.settings=this.clone(result.settings);this.saved=JSON.stringify(result.settings);this.preview=result;this.mode='base';this.checking=false;this.discardEditor();this.notice=id;
      if(history.state?.wbTimeSpecial){history.replaceState({},'','#'+new URLSearchParams({screen:'time',tab:this.tab}));this.lastHash=location.hash;}
    }catch(e){this.blocked||=!!e.blocked;this.error=(e.message||'保存結果を確認できませんでした。')+' 入力を保持しています。'+(this.blocked?'競合または結果不明のため保存を停止しました。入力を控え、再読み込みしてください。':'保存済みの内容を確認するか、再試行してください。');}
    finally{this.busy=false;this.render();}
  }
  bind(){if(!this.host)return;
    this.host.onclick=e=>{const b=e.target.closest('button');if(!b||b.closest('[data-tp-root]')||b.closest('[data-tp-header]')||b.closest('[data-time-basic]')||b.closest('[data-time-basic-toolbar]')||b.closest('[data-ts-panel="events"]')||b.closest('[data-ts-panel="runtime"]')||b.hasAttribute('data-ui-help'))return;e.preventDefault();
      if(b.hasAttribute('data-ts-tab'))this.selectTab(b.dataset.tsTab);
      else if(b.hasAttribute('data-ts-retry'))this.load();
      else if(b.hasAttribute('data-ts-add'))this.open();
      else if(b.hasAttribute('data-ts-edit'))this.open(Number(b.dataset.tsEdit));
      else if(b.hasAttribute('data-ts-delete')){this.guard(()=>{this.settings.specials.splice(Number(b.dataset.tsDelete),1);this.render();this.validate();},()=>{},this.modern);}
      else if(b.hasAttribute('data-ts-save'))this.save();
      else if(b.hasAttribute('data-ts-archive'))this.save('archive_design');
      else if(b.hasAttribute('data-ts-cancel'))this.guard(()=>{this.mode='base';this.restoreCalendar();this.error='';if(history.state?.wbTimeSpecial){history.replaceState({},'','#'+new URLSearchParams({screen:'time',tab:this.tab}));this.lastHash=location.hash;}this.render();this.validate();},()=>{},this.modern);
      else if(b.hasAttribute('data-ts-check')){this.checking=true;this.render();this.validate();}
      else if(b.hasAttribute('data-ts-back')){this.checking=false;this.render();}
      else if(b.hasAttribute('data-ts-add-weekday')){const day=this.host.querySelector('[data-ts-weekday]').value;if(!this.settings.normal.weekdays[day])this.settings.normal.weekdays[day]=this.clone(this.settings.normal.bands);this.render();this.validate();}
      else if(b.hasAttribute('data-ts-remove-weekday')){delete this.settings.normal.weekdays[b.dataset.tsRemoveWeekday];this.render();this.validate();}
      else if(b.hasAttribute('data-ts-add-holiday')){this.settings.normal.holidays.push({date:'',bands:this.clone(this.settings.normal.bands)});this.render();this.validate();}
      else if(b.hasAttribute('data-ts-remove-holiday')){this.settings.normal.holidays.splice(Number(b.dataset.tsRemoveHoliday),1);this.render();this.validate();}
      else if(b.hasAttribute('data-ts-add-band')){this.bandArray(b.dataset.tsAddBand).push({...this.band(),id:'band-'+crypto.randomUUID()});this.render();this.validate();}
      else if(b.hasAttribute('data-ts-remove-band')){const [key,index]=b.dataset.tsRemoveBand.split(':');this.bandArray(key).splice(Number(index),1);this.render();this.validate();}
    };
    this.host.oninput=e=>{
      if(e.target.closest('[data-time-basic]'))return;
      const key=e.target.dataset.tsField;if(key){const path=key.split('.');let value=this.settings;if(path[0]==='special'){value=this.special;path.shift();}for(let i=0;i<path.length-1;i++)value=value[path[i]];const last=path.at(-1);value[last]=e.target.type==='checkbox'?e.target.checked:e.target.type==='number'?(e.target.value===''?'':Number(e.target.value)):e.target.value;
        if(key==='special.selector.kind')this.host.querySelector('[data-ts-selector]').innerHTML=this.selectorForm();this.basic?.updateStatus();this.syncTabs();this.schedulePreview();
      }
    };
    this.host.onchange=e=>{if(e.target.closest('[data-time-basic]'))return;if(e.target.hasAttribute('data-ts-preview-date')){this.previewDate=e.target.value;this.updatePreview();}};
  }
  bandArray(key){return key==='special.bands'?this.special.bands:key.split('.').reduce((v,k)=>v[k],this.settings);}
}


/* Authored evidence is a separate save unit from calendar drafts and run flags. */
class WorldTimePressure {
  constructor(model,onState=()=>{}) {
    this.model=model;this.onState=onState;this.data=null;this.settings=null;this.saved='';
    this.host=null;this.busy=false;this.loading=false;this.blocked=false;this.error='';this.notice='';
  }
  esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  clone(v){return JSON.parse(JSON.stringify(v));}
  legacyDirty(){return !!this.settings&&JSON.stringify(this.settings)!==this.saved;}
  dirty(){return !!this.preparation?.dirty()||this.legacyDirty();}
  restoreLegacy(){if(this.saved)this.settings=JSON.parse(this.saved);this.error='';this.notice='';}
  restore(){this.preparation?.restore();this.restoreLegacy();}
  refreshWorld(model){this.model=model;this.preparation?.refreshWorld(model);if(!this.dirty()&&!this.busy){this.data=null;this.settings=null;}}
  unmount(){this.preparation?.unmount();this.help?.destroy();this.help=null;this.host=null;this.headerHost=null;}
  mount(host,{headerHost=null}={}){if(!host)return;this.host=host;this.headerHost=headerHost;if(!this.data&&!this.loading)this.load();this.render();}
  find(selector){return this.host?.querySelector(selector)||this.headerHost?.querySelector(selector);}
  async request(action,settings){
    let response;
    try {response=await fetch(`/api/worlds/${encodeURIComponent(this.model.id)}/time-pressure${action?'/'+action:''}`,{
      method:settings?'POST':'GET',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},
      ...(settings?{body:JSON.stringify({revision:this.data.revision,world_revision:this.data.world_revision,settings})}:{})});
    } catch(cause){const error=new Error('通信結果を確認できません。');error.blocked=!!settings;throw error;}
    let result;try{result=await response.json();}catch(cause){const error=new Error('応答を確認できません。');error.blocked=!!settings;throw error;}
    if(!response.ok){const error=new Error(result.message||'根拠の保存に失敗しました。');error.blocked=response.status===409||response.status>=500;throw error;}
    if(!result.settings||!Array.isArray(result.settings.sources)||typeof result.revision!=='string'||typeof result.world_revision!=='string'){
      const error=new Error('設定の応答が不正です。');error.blocked=!!settings;throw error;
    }
    return result;
  }
  accept(result){this.data=result;this.settings=this.clone(result.settings);this.saved=JSON.stringify(result.settings);}
  async load(){
    if(this.loading||this.busy)return;this.loading=true;this.error='';this.render();
    try{this.accept(await this.request('',null));this.blocked=false;}
    catch(error){this.error=error.message;}finally{this.loading=false;this.render();}
  }
  options(values,current,blank='選択してください'){
    const missing=current&&!values.some(v=>v.id===current);
    return `<option value="">${blank}</option>`+(missing?`<option value="${this.esc(current)}" selected>${this.esc(current)}（参照先なし）</option>`:'')+
      values.map(v=>`<option value="${this.esc(v.id)}" ${v.id===current?'selected':''}>${this.esc(v.label||v.id)}</option>`).join('');
  }
  select(i,key,label,values,current,required=true){const id=`tp-${i}-${key.replaceAll('.','-')}`;
    return `<div class="ui-field"><label for="${id}">${label}</label><select id="${id}" data-tp-field="${i}.${key}" ${required?'required':''}>${this.options(values,current)}</select></div>`;
  }
  row(source,i){const c=this.data.catalog;
    return `<fieldset><legend>根拠 ${i+1}</legend><div class="ui-field"><label for="tp-${i}-id">根拠ID</label><input id="tp-${i}-id" data-tp-field="${i}.id" value="${this.esc(source.id)}" maxlength="120" required></div>`+
      this.select(i,'anchor.kind','期限の種類',[{id:'goal_period',label:'目標の期間末'},{id:'scheduled_event',label:'予定された出来事の直前'}],source.anchor.kind)+
      this.select(i,'anchor.id','期限の参照先',source.anchor.kind==='goal_period'?c.goals:c.events,source.anchor.id)+
      this.select(i,'response_goal_id','対応する目標',c.goals,source.response_goal_id)+
      this.select(i,'actor_ids','対象人物（初版は主人公）',c.actors,source.actor_ids[0]||'')+
      this.select(i,'awareness.kind','期限と時計を知る根拠',[{id:'public',label:'対象人物へ期限と現在時計を公開'},{id:'known_fact',label:'登録情報を知っている人物に限定'}],source.awareness.kind)+
      (source.awareness.kind==='known_fact'?this.select(i,'awareness.fact_id','認識の根拠となる情報',c.facts,source.awareness.fact_id||''):'')+
      `<div class="ui-field"><label for="tp-${i}-estimate-duration">人物の見積所要時間（tick・任意）</label><input id="tp-${i}-estimate-duration" data-tp-field="${i}.estimate.duration" type="number" min="1" max="10000" step="1" value="${this.esc(source.estimate?.duration??'')}" ${source.estimate?'required':''}></div>`+
      this.select(i,'estimate.fact_id','その見積を知る根拠',c.facts,source.estimate?.fact_id||'',!!source.estimate)+
      '<p class="ui-hint">見積は1〜10,000tick。現在の判断を含め、1なら同じtick、2なら次のtickで完了する見積です。未設定、または根拠の情報を人物が知らない場合は補正しません。</p>'+
      '<p class="ui-hint">目標の期間末を参照する場合、対応する目標は同じIDを選びます。出来事は発生前の判断までを期限とします。</p>'+
      `<button class="ui-button" type="button" data-tp-remove="${i}">この根拠を削除</button></fieldset>`;
  }
  focus(){this.find('[data-tp-error]')?.focus();}
  render(){this.preparation?.unmount();this.help?.destroy();this.help=null;if(!this.host?.isConnected)return;
    if(!this.data){this.host.innerHTML=`<p role="status">${this.loading?'期限と切迫を読み込んでいます…':this.esc(this.error)}</p>${this.loading?'':'<button type="button" data-tp-load>再読み込み</button>'}`;this.bind();this.onRender?.();return;}
    this.host.innerHTML=`<form data-tp-form><h2>期限と切迫</h2><p role="status" data-tp-status>${this.legacyDirty()?'未保存の変更あり':'保存済みの根拠から変更なし'}</p>`+
      `<p class="ui-notice">${this.esc(this.data.availability.reason)}</p><p>登録した根拠：${this.settings.sources.length} / 32件。実行設定のスイッチは別に保存します。</p>`+
      '<span data-tp-help></span>'+
      `<p class="ui-error" role="alert" tabindex="-1" data-tp-error>${this.esc(this.error)}</p>`+
      (this.blocked?'<p class="ui-notice ui-notice--warning">競合または保存結果が不明です。入力を保持しています。入力を控え、再読み込みしてから保存してください。</p>':'')+
      `<div data-tp-rows>${this.settings.sources.map((v,i)=>this.row(v,i)).join('')||'<p>根拠は未登録です。実行設定をオンで保存するには1件以上必要です。</p>'}</div>`+
      '<div class="ui-footer"><button class="ui-button" type="button" data-tp-add>根拠を追加</button><button class="ui-button" type="button" data-tp-reset>変更を戻す</button><button class="ui-button ui-button--confirm" type="submit" data-tp-save>変更を保存</button><button class="ui-button" type="button" data-tp-load>保存済みを再読み込み</button></div>'+
      `<p role="status" data-tp-notice>${this.esc(this.notice)}</p></form>`;
    this.help=window.WorldBloomHelp?.create('期限と切迫の補足','公開を選ぶと、対象人物が参照先の期限と現在の論理時計を認識できる設定として扱います。情報を根拠にする場合は、その情報が人物の知識にある時だけ反映します。実際の計画の所要時間は人物が知る見積とは限らないため、自動入力しません。見積の根拠は期限と時計の公開設定とは別に必要です。設計カレンダーの日付を実行tickへ換算しません。',{theme:this.headerHost?'ui-settings-theme':''});
    if(this.help){this.host.querySelector('[data-tp-help]').append(this.help.button);this.help.button.disabled=this.busy;}
    const disabled=this.busy||!this.model.editable;
    this.host.querySelectorAll('input,select,[data-tp-remove],[data-tp-add],[data-tp-reset],[data-tp-save]').forEach(el=>el.disabled=disabled);
    this.host.querySelector('[data-tp-save]').disabled=disabled||this.blocked||!this.legacyDirty();
    this.host.querySelector('[data-tp-reset]').disabled=disabled||!this.legacyDirty();
    this.host.querySelector('[data-tp-add]').disabled=disabled||this.settings.sources.length>=32;
    this.host.querySelector('[data-tp-load]').disabled=this.busy||this.loading;
    if(!this.model.editable)this.host.querySelector('[data-tp-error]').textContent='閲覧専用です。根拠の変更はできません。';
    if(this.model.purpose_preparation_version===1&&window.WorldBloomPreparationPressure){const legacy=document.createElement('details'),caption=document.createElement('summary');caption.textContent='従来の目標ルールを使う設定';legacy.append(caption);for(const child of Array.from(this.host.children))legacy.append(child);const host=document.createElement('section');host.setAttribute('data-preparation-pressure-host','');this.host.append(host,legacy);this.preparation??=window.WorldBloomPreparationPressure.create({getModel:()=>this.model,getBlocked:()=>this.blocked?'従来設定の保存結果を確認してください。':this.getOtherState?.()||'',onOpenPreparation:()=>this.onOpenPreparation?.(),onState:state=>{this.onPreparationState?.(state);this.onState();},view:this.preparationView??={}});const latest=this.getPreparationSnapshot?.();if(latest)this.preparation.invalidateSaved?.(latest);this.preparation.mount(host);}
    this.bind();this.onRender?.();
  }
  updateButtons(){
    this.notice='';this.host.querySelector('[data-tp-notice]').textContent='';const save=this.find('[data-tp-save]'),reset=this.find('[data-tp-reset]');if(save)save.disabled=this.busy||this.blocked||!this.model.editable||!this.legacyDirty();
    if(reset)reset.disabled=this.busy||!this.model.editable||!this.legacyDirty();
    const status=this.find('[data-tp-status]');if(status)status.textContent=this.legacyDirty()?'未保存の変更あり':'保存済みの根拠から変更なし';this.onState();
  }
  async save(){
    if(this.busy||this.preparation?.busy||this.blocked||!this.model.editable||!this.legacyDirty())return;
    const form=this.host.querySelector('[data-tp-form]');if(!form.reportValidity())return;
    this.busy=true;this.error='';this.notice='';this.onState();this.render();
    try{this.accept(await this.request('',this.settings));this.notice='期限と切迫の根拠を保存しました。';}
    catch(error){this.error=error.message;this.blocked=!!error.blocked;}
    finally{this.busy=false;this.onState();this.render();if(this.error)this.focus();else this.host.querySelector('input,select,[data-tp-add]')?.focus();}
  }
  bind(){
    this.host.querySelector('[data-tp-form]')?.addEventListener('submit',e=>{e.preventDefault();e.stopPropagation();this.save();});
    this.host.onclick=e=>{const b=e.target.closest('button');if(!b||b.disabled)return;
      if(b.hasAttribute('data-tp-load')){if(!this.legacyDirty()||confirm('未保存の入力を破棄して保存済みの根拠を再読み込みしますか？'))this.load();}
      if(b.hasAttribute('data-tp-reset')){if(confirm('期限と切迫の未保存の変更を戻しますか？')){this.restoreLegacy();this.render();this.host.querySelector('input,select,[data-tp-add]')?.focus();}}
      if(b.hasAttribute('data-tp-add')){this.settings.sources.push({id:'source-'+crypto.randomUUID(),anchor:{kind:'goal_period',id:''},response_goal_id:'',actor_ids:[this.data.catalog.actors[0]?.id||''],awareness:{kind:'public'}});this.render();this.host.querySelector(`#tp-${this.settings.sources.length-1}-id`)?.focus();}
      if(b.hasAttribute('data-tp-remove')){this.settings.sources.splice(Number(b.dataset.tpRemove),1);this.render();this.host.querySelector('[data-tp-add]')?.focus();}
    };
    this.host.oninput=e=>{const key=e.target.dataset.tpField;if(!key||e.target.tagName==='SELECT')return;const [i,field,last]=key.split('.'),source=this.settings.sources[Number(i)];
      if(field==='estimate'){
        source.estimate=source.estimate||{duration:'',fact_id:''};source.estimate.duration=e.target.value===''?'':Number(e.target.value);
        if(source.estimate.duration===''&&!source.estimate.fact_id)delete source.estimate;
        const fact=this.host.querySelector(`[data-tp-field="${i}.estimate.fact_id"]`);fact.required=!!source.estimate;e.target.required=!!source.estimate;
      }else source[field]=e.target.value;
      this.updateButtons();};
    this.host.onchange=e=>{const key=e.target.dataset.tpField;if(!key||e.target.tagName!=='SELECT')return;
      const [index,first,last]=key.split('.'),source=this.settings.sources[Number(index)],value=e.target.value;
      if(first==='estimate'){source.estimate=source.estimate||{duration:'',fact_id:''};source.estimate[last]=value;if(source.estimate.duration===''&&!source.estimate.fact_id)delete source.estimate;}
      else if(first==='actor_ids')source.actor_ids=value?[value]:[];else if(last)source[first][last]=value;else source[first]=value;
      if(first==='awareness'&&last==='kind'){source.awareness=value==='public'?{kind:'public'}:{kind:'known_fact',fact_id:''};}
      if(first==='anchor'&&last==='kind')source.anchor.id='';
      if(first==='anchor'&&last==='id'&&source.anchor.kind==='goal_period')source.response_goal_id=value;
      this.render();this.host.querySelector(`[data-tp-field="${key}"]`)?.focus();
    };
  }
}
