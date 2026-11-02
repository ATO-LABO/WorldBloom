/* Calendar designs are sidecars; the slot engine never reads these drafts. */
'use strict';
class WorldTimeSettings {
  constructor(model) {
    this.model=model;this.data=null;this.settings=null;this.saved='';this.mode='base';
    this.selected=null;this.special=null;this.originalSpecial='';this.busy=false;this.error='';
    this.notice='';this.preview=null;this.host=null;this.loading=false;this.checking=false;
    this.api=`/api/worlds/${encodeURIComponent(model.id)}/time-settings`;
    this.timer=null;this.seq=0;this.overridesOpen=false;
    this.pressure=new WorldTimePressure(model,()=>this.syncPressureTabs());this.frameHost=null;this.pressureTab="calendar";
    document.addEventListener('click',e=>{
      const link=e.target.closest('a[href]');if(!link||!this.dirty()||link.target==='_blank'||e.ctrlKey||e.metaKey)return;
      e.preventDefault();e.stopImmediatePropagation();this.guard(()=>{this.leave();location.href=link.href;});
    },true);
    document.addEventListener('change',e=>{
      const picker=e.target.closest('[data-wb="world-picker"]');if(!picker||!this.dirty())return;
      const value=picker.value;e.stopImmediatePropagation();picker.value=this.model.id;
      this.guard(()=>{this.leave();location.href=`/worlds/${encodeURIComponent(value)}`;});
    },true);
    addEventListener('beforeunload',e=>{if(this.dirty()||this.busy||this.pressure.busy){e.preventDefault();e.returnValue='';}});
    addEventListener('popstate',()=>{
      if(this.mode==='special')this.guard(()=>{this.discardEditor();this.mode='base';this.render();},()=>{
        history.pushState({wbTimeSpecial:true},'',`${location.pathname}${location.search}#time-special`);
      });
    });
  }
  esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  clone(v){return JSON.parse(JSON.stringify(v));}
  dirty(){return this.pressure.dirty()||!!this.settings&&(JSON.stringify(this.settings)!==this.saved||
    (this.mode==='special'&&JSON.stringify(this.special)!==this.originalSpecial));}
  guard(action,onContinue=()=>{}){
    if(this.busy||this.pressure.busy)return;
    if(!this.dirty()){action();return;}
    if(this.confirm?.open)return;
    const dialog=document.createElement('dialog');dialog.className='wp-dialog ts-confirm';
    dialog.innerHTML='<h2>未保存の変更があります</h2><p>変更を破棄して戻りますか？保存済みの内容は保持します。</p><div class="ts-actions"><button data-continue>編集を続ける</button><button data-discard>変更を破棄して戻る</button></div>';
    document.body.append(dialog);this.confirm=dialog;
    const close=()=>{dialog.close();dialog.remove();this.confirm=null;};
    dialog.querySelector('[data-continue]').onclick=()=>{close();onContinue();};
    dialog.querySelector('[data-discard]').onclick=()=>{close();this.restore();action();};
    dialog.addEventListener('cancel',e=>{e.preventDefault();close();onContinue();});dialog.showModal();
  }
  restore(){this.pressure.restore();if(this.saved)this.settings=JSON.parse(this.saved);this.discardEditor();}
  discardEditor(){this.special=null;this.originalSpecial='';this.selected=null;}
  leave(){this.pressure.unmount();this.frameHost=null;if(history.state?.wbTimeSpecial)history.replaceState({},'',`${location.pathname}${location.search}`);this.mode='base';this.checking=false;this.discardEditor();this.host=null;}
  refreshWorld(model){this.pressure.refreshWorld(model);this.model=model;this.data=null;this.preview=null;if(!this.dirty())this.settings=null;}
  mount(host){
    if(this.frameHost!==host){
      this.frameHost=host;
      host.innerHTML='<div class="ui-tabs" role="tablist" aria-label="時間と出来事"><button type="button" role="tab" data-ts-pressure-tab="calendar" id="ts-calendar-tab" aria-controls="ts-calendar-panel">時間の設計</button><button type="button" role="tab" data-ts-pressure-tab="pressure" id="ts-pressure-tab" aria-controls="ts-pressure-panel">期限と切迫</button></div><section data-ts-calendar-panel id="ts-calendar-panel" role="tabpanel" aria-labelledby="ts-calendar-tab"></section><section data-tp-root id="ts-pressure-panel" role="tabpanel" aria-labelledby="ts-pressure-tab" hidden></section>';
      this.host=host.querySelector('[data-ts-calendar-panel]');
      const buttons=[...host.querySelectorAll('[data-ts-pressure-tab]')];
      buttons.forEach((button,index)=>{
        button.onclick=()=>this.selectPressureTab(button.dataset.tsPressureTab);
        button.onkeydown=event=>{
          let next;if(event.key==='Home')next=0;else if(event.key==='End')next=buttons.length-1;
          else if(event.key==='ArrowRight')next=(index+1)%buttons.length;else if(event.key==='ArrowLeft')next=(index+buttons.length-1)%buttons.length;
          if(next!==undefined){event.preventDefault();this.selectPressureTab(buttons[next].dataset.tsPressureTab);buttons[next].focus();}
        };
      });
      this.selectPressureTab(new URLSearchParams(location.hash.slice(1)).get('tab')==='pressure'?'pressure':'calendar',false);
    }
    if(!this.data&&!this.loading)this.load();this.render();
  }
  selectPressureTab(tab,write=true){
    if(!['calendar','pressure'].includes(tab)||this.busy||this.pressure.busy)return;
    this.pressureTab=tab;this.syncPressureTabs();
    if(tab==='pressure')this.pressure.mount(this.frameHost?.querySelector('[data-tp-root]'));
    if(write)history.replaceState(history.state||{},'','#'+new URLSearchParams({screen:'time',tab}));
  }
  syncPressureTabs(){
    if(!this.frameHost)return;
    this.frameHost.querySelectorAll('[data-ts-pressure-tab]').forEach(button=>{
      const active=button.dataset.tsPressureTab===this.pressureTab;
      button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
      button.disabled=this.busy||this.pressure.busy;
    });
    const calendar=this.frameHost.querySelector('[data-ts-calendar-panel]'),pressure=this.frameHost.querySelector('[data-tp-root]');
    if(calendar)calendar.hidden=this.pressureTab!=='calendar';if(pressure)pressure.hidden=this.pressureTab!=='pressure';
  }
  async request(action,settings){
    const response=await fetch(this.api+(action?'/'+action:''),{method:settings?'POST':'GET',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},...(settings?{body:JSON.stringify({revision:this.data.revision,settings})}:{})});
    const result=await response.json();if(!response.ok)throw new Error(result.message||'保存できませんでした。');
    return result;
  }
  async load(){this.loading=true;this.error='';try{
    const result=await this.request('',null);if(!result.settings||typeof result.revision!=='string')throw new Error('設定の応答が不正です。');
    this.data=result;this.settings=this.clone(result.settings);this.saved=JSON.stringify(result.settings);this.preview=result;
  }catch(e){this.error=e.message||'設定を読み込めませんでした。';}finally{this.loading=false;this.render();}}
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
  async validate(){if(!this.data)return;const token=++this.seq;try{const result=await this.request('validate',this.currentSettings());if(token===this.seq){this.preview=result;this.updatePreview();}}catch(e){if(token===this.seq){this.error=e.message;this.updatePreview();}}}
  updatePreview(){this.host?.querySelectorAll('[data-ts-preview]').forEach(e=>{e.innerHTML=this.previewHtml();});this.host?.querySelector('[data-ts-message]')?.replaceChildren(document.createTextNode(this.error));this.host?.querySelectorAll('[data-ts-edit]').forEach(b=>{const id=this.settings.specials[Number(b.dataset.tsEdit)]?.id;const old=b.querySelector('.ts-error');old?.remove();if(this.sourcesConflict(id))b.insertAdjacentHTML('beforeend','<small class="ts-error">同順位競合 · 適用未確定</small>');});}
  schedulePreview(){clearTimeout(this.timer);this.timer=setTimeout(()=>this.validate(),180);}
  render(){if(!this.host?.isConnected)return;const overrides=this.host.querySelector('[data-ts-overrides]');if(overrides)this.overridesOpen=overrides.open;
    if(!this.data){this.host.innerHTML=`<p>${this.loading?'時間設定の下書きを読み込んでいます…':this.esc(this.error)}</p>${this.loading?'':'<button data-ts-retry>再試行</button>'}`;this.bind();return;}
    const disabled=this.busy||!this.model.editable?'disabled':'';
    const banner='<p class="ts-banner">設計用下書き・適用プレビューです。可変時間の実行エンジンは未対応です。下書き保存や設計版の記録では、既存の世界・実行・ログ・再開設定を変更しません。</p>';
    const message=`<p data-ts-message role="status" class="ts-error">${this.esc(this.error)}</p>`;
    const actions=`<div class="ts-actions"><button data-ts-save ${disabled}>${this.busy?'保存中…':'下書き保存'}</button><button data-ts-cancel ${disabled}>キャンセル</button></div>`;
    if(this.mode==='special'){
      const s=this.special;this.host.innerHTML=banner+`<div class="ts-editor"><section><h2>特別日一覧</h2>${this.list(true)}<button data-ts-add ${disabled}>特別日を追加</button></section><section><h2>選択中の特別日を編集</h2>${this.field('special.name','名前',s.name)}<label><input data-ts-field="special.enabled" type="checkbox" ${s.enabled?'checked':''}> 有効</label><label>対象日<select data-ts-field="special.selector.kind">${[['date','単日'],['range','日付範囲'],['first','初日から'],['last','最終日から']].map(([v,l])=>`<option value="${v}" ${s.selector.kind===v?'selected':''}>${l}</option>`).join('')}</select></label><div data-ts-selector>${this.selectorForm()}</div>${this.field('special.priority','優先順位（大きい値を優先）',s.priority,'number')}${this.bandsForm(s.bands,'special.bands')}${message}${actions}</section><aside><h2>適用結果プレビュー</h2><div data-ts-preview>${this.previewHtml()}</div></aside></div>`;
    }else if(this.checking){this.host.innerHTML=banner+`<section><h2>設定を確認</h2><p>保存済み下書きとの変更点</p><pre class="ts-diff">${this.esc(this.diff())}</pre><div data-ts-preview>${this.previewHtml()}</div><button data-ts-archive ${disabled}>設計版を記録（実行には使えません）</button><button disabled>実行用の新しい版として確定（未対応）</button><button data-ts-back>基本画面に戻る</button>${message}<h3>記録済みの設計版</h3>${this.versionsHtml()}</section>`;
    }else{this.host.innerHTML=banner+`<section class="wp-section" data-ts-normal><h2>期間と通常日の時間設定</h2><div class="ts-pair">${this.field('period.start_date','開始日（未確定なら空欄）',this.settings.period.start_date,'date')}${this.field('period.end_date','最終日（未確定なら空欄）',this.settings.period.end_date,'date')}</div>${this.bandsForm(this.settings.normal.bands,'normal.bands')}${this.calendarOverrides()}${actions}</section><section class="wp-section" data-ts-specials><h2>特別日一覧</h2>${this.list()}<button data-ts-add ${disabled}>特別日を追加</button></section><section class="wp-section"><h2>日付別の適用プレビュー</h2><div data-ts-preview>${this.previewHtml()}</div><button data-ts-check ${disabled}>設定を確認</button>${message}${this.notice?'<p role="status">下書きを保存しました。</p>':''}</section>`;}
    if(this.busy||!this.model.editable)this.host.querySelectorAll('input,textarea,select,button').forEach(el=>el.disabled=true);
    this.bind();
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
  open(index=null){this.guard(()=>{
    this.mode='special';this.checking=false;this.selected=index;
    this.special=index===null?{id:'special-'+crypto.randomUUID(),name:'',enabled:true,priority:10,selector:{kind:'date',start:'',end:'',offset:0},bands:[this.clone(this.settings.normal.bands[0]||this.band())]}:this.clone(this.settings.specials[index]);
    this.originalSpecial=JSON.stringify(this.special);this.error='';
    if(!history.state?.wbTimeSpecial)history.pushState({wbTimeSpecial:true},'',`${location.pathname}${location.search}#time-special`);
    this.render();this.validate();
  });}
  async save(action=''){
    if(this.busy||!this.model.editable){this.render();return;}const settings=this.currentSettings();
    this.busy=true;clearTimeout(this.timer);this.seq++;this.error='';this.render();
    try{if(action==='archive_design'){const draft=await this.request('',settings);this.data=draft;this.saved=JSON.stringify(draft.settings);}const result=await this.request(action,settings);if(!result.settings||typeof result.revision!=='string')throw new Error('保存結果が不正です。');
      const id=this.special?.id||'saved';this.data=result;this.settings=this.clone(result.settings);this.saved=JSON.stringify(result.settings);this.preview=result;this.mode='base';this.checking=false;this.discardEditor();this.notice=id;
      if(history.state?.wbTimeSpecial)history.replaceState({},'',`${location.pathname}${location.search}#time`);
    }catch(e){this.error=(e.message||'保存結果を確認できませんでした。')+' 入力を保持しています。保存済みの内容を確認するか、再試行してください。';}
    finally{this.busy=false;this.render();}
  }
  bind(){if(!this.host)return;
    this.host.onclick=e=>{const b=e.target.closest('button');if(!b)return;e.preventDefault();
      if(b.hasAttribute('data-ts-retry'))this.load();
      else if(b.hasAttribute('data-ts-add'))this.open();
      else if(b.hasAttribute('data-ts-edit'))this.open(Number(b.dataset.tsEdit));
      else if(b.hasAttribute('data-ts-delete')){this.guard(()=>{this.settings.specials.splice(Number(b.dataset.tsDelete),1);this.render();this.validate();});}
      else if(b.hasAttribute('data-ts-save'))this.save();
      else if(b.hasAttribute('data-ts-archive'))this.save('archive_design');
      else if(b.hasAttribute('data-ts-cancel'))this.guard(()=>{this.mode='base';this.restore();this.error='';if(history.state?.wbTimeSpecial)history.replaceState({},'',`${location.pathname}${location.search}#time`);this.render();this.validate();});
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
      const key=e.target.dataset.tsField;if(key){const path=key.split('.');let value=this.settings;if(path[0]==='special'){value=this.special;path.shift();}for(let i=0;i<path.length-1;i++)value=value[path[i]];const last=path.at(-1);value[last]=e.target.type==='checkbox'?e.target.checked:e.target.type==='number'?(e.target.value===''?'':Number(e.target.value)):e.target.value;
        if(key==='special.selector.kind')this.host.querySelector('[data-ts-selector]').innerHTML=this.selectorForm();this.schedulePreview();
      }
    };
    this.host.onchange=e=>{if(e.target.hasAttribute('data-ts-preview-date')){this.previewDate=e.target.value;this.updatePreview();}};
  }
  bandArray(key){return key==='special.bands'?this.special.bands:key.split('.').reduce((v,k)=>v[k],this.settings);}
}

class WorldTimePressure {
  constructor(model,onState=()=>{}) {
    this.model=model;this.onState=onState;this.data=null;this.settings=null;this.saved='';
    this.host=null;this.busy=false;this.loading=false;this.blocked=false;this.error='';this.notice='';
  }
  esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  clone(v){return JSON.parse(JSON.stringify(v));}
  dirty(){return !!this.settings&&JSON.stringify(this.settings)!==this.saved;}
  restore(){if(this.saved)this.settings=JSON.parse(this.saved);this.error='';this.notice='';}
  refreshWorld(model){this.model=model;if(!this.dirty()&&!this.busy){this.data=null;this.settings=null;}}
  unmount(){this.help?.destroy();this.help=null;this.host=null;}
  mount(host){if(!host)return;this.host=host;if(!this.data&&!this.loading)this.load();this.render();}
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
  focus(){this.host?.querySelector('[data-tp-error]')?.focus();}
  render(){this.help?.destroy();this.help=null;if(!this.host?.isConnected)return;
    if(!this.data){this.host.innerHTML=`<p role="status">${this.loading?'期限と切迫を読み込んでいます…':this.esc(this.error)}</p>${this.loading?'':'<button type="button" data-tp-load>再読み込み</button>'}`;this.bind();return;}
    this.host.innerHTML=`<form data-tp-form><h2>期限と切迫</h2><p role="status">${this.dirty()?'未保存の変更あり':'保存済みの根拠から変更なし'}</p>`+
      `<p class="ui-notice">${this.esc(this.data.availability.reason)}</p><p>登録した根拠：${this.settings.sources.length} / 32件。実行設定のスイッチは別に保存します。</p>`+
      '<span data-tp-help></span>'+
      `<p class="ui-error" role="alert" tabindex="-1" data-tp-error>${this.esc(this.error)}</p>`+
      (this.blocked?'<p class="ui-notice ui-notice--warning">競合または保存結果が不明です。入力を保持しています。入力を控え、再読み込みしてから保存してください。</p>':'')+
      `<div data-tp-rows>${this.settings.sources.map((v,i)=>this.row(v,i)).join('')||'<p>根拠は未登録です。実行設定をオンで保存するには1件以上必要です。</p>'}</div>`+
      '<div class="ui-footer"><button class="ui-button" type="button" data-tp-add>根拠を追加</button><button class="ui-button" type="button" data-tp-reset>変更を戻す</button><button class="ui-button ui-button--confirm" type="submit" data-tp-save>変更を保存</button><button class="ui-button" type="button" data-tp-load>保存済みを再読み込み</button></div>'+
      `<p role="status" data-tp-notice>${this.esc(this.notice)}</p></form>`;
    this.help=window.WorldBloomHelp?.create('期限と切迫の補足','公開を選ぶと、対象人物が参照先の期限と現在の論理時計を認識できる設定として扱います。情報を根拠にする場合は、その情報が人物の知識にある時だけ反映します。実際の計画の所要時間は人物が知る見積とは限らないため、自動入力しません。見積の根拠は期限と時計の公開設定とは別に必要です。設計カレンダーの日付を実行tickへ換算しません。');
    if(this.help){this.host.querySelector('[data-tp-help]').append(this.help.button);this.help.button.disabled=this.busy;}else this.host.querySelector('[data-tp-help]').textContent='公開は期限と現在時計の認識を宣言します。見積は別の登録情報を知る場合だけ使い、設計カレンダーから換算しません。';
    const disabled=this.busy||!this.model.editable;
    this.host.querySelectorAll('input,select,[data-tp-remove],[data-tp-add],[data-tp-reset],[data-tp-save]').forEach(el=>el.disabled=disabled);
    this.host.querySelector('[data-tp-save]').disabled=disabled||this.blocked||!this.dirty();
    this.host.querySelector('[data-tp-reset]').disabled=disabled||!this.dirty();
    this.host.querySelector('[data-tp-add]').disabled=disabled||this.settings.sources.length>=32;
    this.host.querySelector('[data-tp-load]').disabled=this.busy||this.loading;
    if(!this.model.editable)this.host.querySelector('[data-tp-error]').textContent='閲覧専用です。根拠の変更はできません。';
    this.bind();
  }
  updateButtons(){
    this.notice='';this.host.querySelector('[data-tp-notice]').textContent='';this.host.querySelector('[data-tp-save]').disabled=this.busy||this.blocked||!this.model.editable||!this.dirty();
    this.host.querySelector('[data-tp-reset]').disabled=this.busy||!this.model.editable||!this.dirty();
    this.host.querySelector('[role="status"]').textContent=this.dirty()?'未保存の変更あり':'保存済みの根拠から変更なし';
  }
  async save(){
    if(this.busy||this.blocked||!this.model.editable||!this.dirty())return;
    const form=this.host.querySelector('form');if(!form.reportValidity())return;
    this.busy=true;this.error='';this.notice='';this.onState();this.render();
    try{this.accept(await this.request('',this.settings));this.notice='期限と切迫の根拠を保存しました。';}
    catch(error){this.error=error.message;this.blocked=!!error.blocked;}
    finally{this.busy=false;this.onState();this.render();if(this.error)this.focus();else this.host.querySelector('input,select,[data-tp-add]')?.focus();}
  }
  bind(){
    this.host.querySelector('[data-tp-form]')?.addEventListener('submit',e=>{e.preventDefault();e.stopPropagation();this.save();});
    this.host.onclick=e=>{const b=e.target.closest('button');if(!b||b.disabled)return;
      if(b.hasAttribute('data-tp-load')){if(!this.dirty()||confirm('未保存の入力を破棄して保存済みの根拠を再読み込みしますか？'))this.load();}
      if(b.hasAttribute('data-tp-reset')){if(confirm('期限と切迫の未保存の変更を戻しますか？')){this.restore();this.render();this.host.querySelector('input,select,[data-tp-add]')?.focus();}}
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
