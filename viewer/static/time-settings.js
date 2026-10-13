/* Calendar designs are sidecars; the slot engine never reads these drafts. */
'use strict';
class WorldTimeSettings {
  constructor(model) {
    this.model=model;this.data=null;this.settings=null;this.saved='';this.mode='base';
    this.selected=null;this.special=null;this.originalSpecial='';this.busy=false;this.error='';
    this.notice='';this.preview=null;this.host=null;this.loading=false;this.checking=false;
    this.api=`/api/worlds/${encodeURIComponent(model.id)}/time-settings`;
    this.timer=null;this.seq=0;this.overridesOpen=false;
    document.addEventListener('click',e=>{
      const link=e.target.closest('a[href]');if(!link||!this.dirty()||link.target==='_blank'||e.ctrlKey||e.metaKey)return;
      e.preventDefault();e.stopImmediatePropagation();this.guard(()=>{this.leave();location.href=link.href;});
    },true);
    document.addEventListener('change',e=>{
      const picker=e.target.closest('[data-wb="world-picker"]');if(!picker||!this.dirty())return;
      const value=picker.value;e.stopImmediatePropagation();picker.value=this.model.id;
      this.guard(()=>{this.leave();location.href=`/worlds/${encodeURIComponent(value)}`;});
    },true);
    addEventListener('beforeunload',e=>{if(this.dirty()||this.busy){e.preventDefault();e.returnValue='';}});
    addEventListener('popstate',()=>{
      if(this.mode==='special')this.guard(()=>{this.discardEditor();this.mode='base';this.render();},()=>{
        history.pushState({wbTimeSpecial:true},'',`${location.pathname}${location.search}#time-special`);
      });
    });
  }
  esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  clone(v){return JSON.parse(JSON.stringify(v));}
  dirty(){return !!this.settings&&(JSON.stringify(this.settings)!==this.saved||
    (this.mode==='special'&&JSON.stringify(this.special)!==this.originalSpecial));}
  guard(action,onContinue=()=>{}){
    if(this.busy)return;
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
  restore(){if(this.saved)this.settings=JSON.parse(this.saved);this.discardEditor();}
  discardEditor(){this.special=null;this.originalSpecial='';this.selected=null;}
  leave(){if(history.state?.wbTimeSpecial)history.replaceState({},'',`${location.pathname}${location.search}`);this.mode='base';this.checking=false;this.discardEditor();this.host=null;}
  refreshWorld(model){this.model=model;this.data=null;this.preview=null;if(!this.dirty())this.settings=null;}
  mount(host){this.host=host;if(!this.data&&!this.loading)this.load();this.render();}
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
