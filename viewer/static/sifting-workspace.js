"use strict";
(() => {
 const root=document.querySelector('[data-sifting]'); if(!root)return;
 const initial=JSON.parse(root.dataset.initial||'{}'), $=s=>root.querySelector(s), $$=s=>[...root.querySelectorAll(s)];
 const labels={unclassified:'未分類',held:'保留',adopted:'採用',rejected:'除外'};
 const items=new Map((initial.items||[]).map(c=>[c.candidate_id,c]));
 const visible=initial.visible||[], pending=new Map(), chosen=new Set();
 let current=null, revision=initial.revision, saving=false, failed=false, mode='browse', timer=null, flushPromise=null;
 const storageKey=`sf:${initial.run_id}:${initial.view}`, notice=$('[data-sf-notice]');
 function tell(text){notice.textContent=text;notice.hidden=!text;}
 async function api(path,body){
  const response=await fetch(path,{method:body?'POST':'GET',headers:{'X-WorldBloom-Client':'1',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  const value=await response.json();return {ok:response.ok,status:response.status,value};
 }
 const endpoint=`/api/runs/${encodeURIComponent(initial.run_id)}/selection`;
 function savedState(){try{return JSON.parse(sessionStorage.getItem(storageKey)||'{}');}catch{return {};}}
 function remember(){try{sessionStorage.setItem(storageKey,JSON.stringify({candidate:current,panel:$('[data-sf-tab][aria-selected="true"]')?.dataset.sfTab,scroll:$('[data-sf-list]')?.scrollTop||$('.sf-grid-scroll')?.scrollTop||0}));}catch{}}
 function saveDraft(){try{sessionStorage.setItem(storageKey+':draft',JSON.stringify([...pending]));}catch{}}
 function status(text){const e=$('[data-sf-save]');if(e)e.textContent=text;}
 function counts(){return [...items.values()].filter(c=>c.state==='adopted').length;}
 function footer(){
  const n=counts(), counter=$('[data-sf-adopted]');if(counter)counter.textContent=n;
  const button=$('[data-sf-proceed]');
  if(button){button.disabled=saving||pending.size>0||failed||(mode==='browse'?n===0:chosen.size===0||(mode==='compare'&&(chosen.size<2||chosen.size>4)));
   button.textContent=mode==='browse'?`採用候補を確認（${n}件） →`:mode==='compare'?`選んだ${chosen.size}件を比較`:`あらすじの生成対象を確認（${chosen.size}件） →`;
   $('[data-sf-footer-note]').textContent=mode==='browse'?(n?'次の画面で対象を確認します':'候補を採用すると次へ進めます'):`対象 ${chosen.size}件（採用とは別の選択です）`;
  }
  const generate=$('[data-sf-generate]');if(generate)generate.disabled=!initial.request||saving||pending.size>0||failed;
 }
 function rowState(c){
  const row=$$('[data-candidate-id]').find(e=>e.dataset.candidateId===c.candidate_id), badge=row?.querySelector('[data-row-state]');
  if(badge){badge.textContent=labels[c.state];badge.className=`sf-badge sf-${c.state}`;}
 }
 function field(name,value){const e=$(`[data-sf-${name}]`);if(e)e.textContent=value??'—';}
 function href(name,url,label){const e=$(`[data-sf-${name}]`);if(!e)return;e.hidden=!url;if(url)e.href=url;if(label)e.textContent=label;}
 function select(id,focus=false){
  const c=items.get(id);if(!c)return;current=id;
  for(const row of $$('[data-candidate-id]')){const active=row.dataset.candidateId===id;row.classList.toggle('is-current',active);row.querySelector('[data-sf-open]')?.setAttribute('aria-pressed',String(active));}
  field('title',c.label);field('meta',`${labels[c.state]} · 品質 ${c.quality_text} · ${c.reached?'結末に到達':'未到達'}`);
  field('story',c.synopsis||`${c.synopsis_state}。記録から内容を確認できます。`);
  field('ending',c.ending_text);
  const note=$('[data-sf-note]');if(note){note.value=pending.get(id)?.note??c.note;note.disabled=initial.running||mode!=='browse';}
  href('detail',c.detail_href||c.raw_href,c.detail_href?'物語と根拠を詳しく読む ↗':'原記録から内容を確認 ↗');href('raw',c.raw_href);href('output',c.output_href);
  href('synopsis',c.can_synopsis&&mode==='browse'?`/runs/${encodeURIComponent(initial.run_id)}/generate?kind=synopsize&candidate=${encodeURIComponent(id)}`:null);
  const data=$('[data-sf-data]');if(data){data.replaceChildren();for(const [label,value] of [['候補ID',id],['区画',c.cell_key],['世代',c.generation],['seed',c.seed],['原記録',c.availability]]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value??'—';data.append(dt,dd);}}
  for(const radio of $$('[name="sf-verdict"]')){radio.checked=radio.value===(pending.get(id)?.state??c.state);radio.disabled=initial.running||mode!=='browse'||(radio.value==='adopted'&&!c.screenable);}
  field('reason',!c.screenable?`採用できません：${c.reached?c.availability:'結末に未到達'}`:'');
  const i=visible.indexOf(id);if($('[data-sf-prev]'))$('[data-sf-prev]').disabled=i<=0;if($('[data-sf-next]'))$('[data-sf-next]').disabled=i<0||i>=visible.length-1;
  const url=new URL(location.href);url.searchParams.set('candidate',id);history.replaceState(null,'',url);remember();
  if(focus){root.classList.add('sf-show-detail');$('[data-sf-title]').setAttribute('tabindex','-1');$('[data-sf-title]').focus();}
 }
 function update(id,change){pending.set(id,{...pending.get(id),...change});saveDraft();status('未保存');footer();}
 function applyRemote(value){revision=value.revision;const entries=new Map(value.entries.map(e=>[e.candidate_id,e]));for(const c of items.values()){const e=entries.get(c.candidate_id);c.state=e?.state||'unclassified';c.note=e?.note||'';rowState(c);}footer();}
 async function reconcile(){const r=await api(endpoint);if(!r.ok)throw Error(r.value.message||'現在の判定を確認できません');applyRemote(r.value);
  for(const [id,change] of pending){const c=items.get(id);for(const key of Object.keys(change))if(c?.[key]===change[key])delete change[key];if(!Object.keys(change).length)pending.delete(id);}
  saveDraft();
 }
 function flush(){
  clearTimeout(timer);if(saving)return flushPromise;if(failed||!pending.size)return Promise.resolve(!failed);
  saving=true;footer();status('保存中…');
  flushPromise=(async()=>{try{
   while(pending.size){
    const [id,change]=pending.entries().next().value, sent={...change};
    const r=await api(endpoint,{expected_revision:revision,changes:[{candidate_id:id,...sent}]});
    if(!r.ok){
     if(r.value.code==='projection_pending'){await reconcile();tell('判定は保存済みです。旧表示への反映を再照合してください。');if(pending.size)throw Error('未保存の入力を確認してください。');break;}
     throw Error(r.status===409?'別の画面で更新されています。「再試行」で最新の判定を確認して保存してください。':r.value.message||'保存できませんでした');
    }
    applyRemote(r.value);
    const next=pending.get(id);for(const key of Object.keys(sent))if(next?.[key]===sent[key])delete next[key];if(next&&!Object.keys(next).length)pending.delete(id);
    saveDraft();
   }
   status('保存しました');if(current)select(current);const filter=initial.query?.state?.[0];if(filter&&[...items.values()].some(c=>visible.includes(c.candidate_id)&&c.state!==filter))tell('判定を変更しました。現在の一覧はそのまま表示しています。絞り込みを選び直すと更新されます。');return true;
  }catch(e){failed=true;status(e.message);tell(e.message);const retry=$('[data-sf-retry]');if(retry)retry.hidden=false;return false;}
  finally{saving=false;footer();}})();return flushPromise;
 }
 $$('[data-sf-open]').forEach(b=>b.addEventListener('click',()=>select(b.dataset.sfOpen,true)));
 $$('[data-sf-tab]').forEach(b=>b.addEventListener('click',()=>{for(const tab of $$('[data-sf-tab]'))tab.setAttribute('aria-selected',String(tab===b));for(const panel of $$('[data-sf-panel]'))panel.hidden=panel.dataset.sfPanel!==b.dataset.sfTab;remember();}));
 $$('[data-sf-tab]').forEach(b=>b.addEventListener('keydown',e=>{const tabs=$$('[data-sf-tab]'),index=tabs.indexOf(b);let next;if(e.key==='ArrowRight')next=(index+1)%tabs.length;else if(e.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=tabs.length-1;else return;e.preventDefault();tabs[next].click();tabs[next].focus();}));
 $$('[name="sf-verdict"]').forEach(r=>r.addEventListener('change',()=>{if(current){update(current,{state:r.value});flush();}}));
 $('[data-sf-note]')?.addEventListener('input',e=>{if(!current)return;update(current,{note:e.target.value});clearTimeout(timer);timer=setTimeout(flush,600);});
 $('[data-sf-note]')?.addEventListener('blur',()=>flush());
 $('[data-sf-retry]')?.addEventListener('click',async()=>{try{await reconcile();failed=false;$('[data-sf-retry]').hidden=true;tell('');await flush();if(!pending.size){if(initial.view==='tray'){location.reload();return;}status('保存済みの判定を確認しました');footer();}}catch(e){tell(e.message);}});
 $('[data-sf-prev]')?.addEventListener('click',()=>select(visible[visible.indexOf(current)-1],true));
 $('[data-sf-next]')?.addEventListener('click',()=>select(visible[visible.indexOf(current)+1],true));
 $('[data-sf-back]')?.addEventListener('click',()=>root.classList.remove('sf-show-detail'));
 $('[data-sf-fit]')?.addEventListener('click',e=>{root.classList.toggle('sf-grid-fit');e.target.textContent=root.classList.contains('sf-grid-fit')?'読みやすい大きさに戻す':'格子を全体表示';});
 async function switchMode(next){if(pending.size&&!await flush())return;mode=next;chosen.clear();
  for(const choice of $$('.sf-choice')){choice.hidden=mode==='browse';const input=choice.querySelector('input');input.checked=false;const c=items.get(input.dataset.sfChoice);input.disabled=mode==='synopsis'?!c.can_synopsis:!c.representative;input.title=input.disabled?(initial.running?'実行中のため変更できません':!c.screenable?(c.unavailable_reason||c.availability):c.synopsis_state):'対象にする';}
  const bar=$('[data-sf-mode-note]');bar.hidden=mode==='browse';bar.querySelector('span').textContent=mode==='compare'?'比較する区画を2〜4件選んでください':'あらすじを準備する候補を選んでください';
  $('[data-sf-verdict]').hidden=mode!=='browse';if(current)select(current);footer();
 }
 $$('[data-sf-mode]').forEach(b=>b.addEventListener('click',()=>switchMode(b.dataset.sfMode)));
 $$('[data-sf-choice]').forEach(c=>c.addEventListener('change',()=>{if(c.checked)chosen.add(c.dataset.sfChoice);else chosen.delete(c.dataset.sfChoice);footer();}));
 $('[data-sf-clear]')?.addEventListener('click',()=>{chosen.clear();$$('[data-sf-choice]').forEach(c=>c.checked=false);footer();});
 $('[data-sf-proceed]')?.addEventListener('click',async()=>{
  if(pending.size&&!await flush())return;remember();
  if(mode==='browse'){location.href=`/selected?run=${encodeURIComponent(initial.run_id)}`;return;}
  const qs=new URLSearchParams();
  if(mode==='synopsis'){qs.set('kind','synopsize');for(const id of chosen)qs.append('candidate',id);await window.WBGeneration.open(`/runs/${encodeURIComponent(initial.run_id)}/generate?${qs}`,$('[data-sf-proceed]'));return;}
  try{const r=await api(`/api/runs/${encodeURIComponent(initial.run_id)}/candidates`);if(!r.ok||r.value.revision!==initial.publication){tell('公開版が更新されています。格子を再読み込みして比較対象を確認してください。');return;}
   const path=location.pathname+'/compare';for(const id of chosen){qs.append('cell',items.get(id).cell_key);qs.append('candidate',id);}qs.set('publication',initial.publication);location.href=path+'?'+qs;
  }catch{tell('比較する公開版を確認できません。');}
 });
 $('[data-sf-run]')?.addEventListener('change',async e=>{if(pending.size&&!await flush())return;remember();location.href=e.target.value;});
 $$('[data-sf-tray-state]').forEach(b=>b.addEventListener('click',async()=>{
  b.disabled=true;update(b.dataset.sfTrayState,{state:b.dataset.state});initial.request=null;
  if(await flush())location.reload();else b.disabled=false;
 }));
 let submitting=false;
 $('[data-sf-generate]')?.addEventListener('click',async e=>{
  if(submitting||!initial.request)return;const ack=$('[data-sf-ack]');if(ack&&!ack.checked){tell('結果不明の再生成について確認してください。');return;}
  submitting=true;e.target.disabled=true;
  try{const check=await api(endpoint);if(!check.ok||check.value.revision!==initial.request.selection_revision){initial.request=null;tell('採用候補が更新されています。再読み込みして対象を確認してください。');return;}
   await window.WBGeneration.start(initial.request,e.target);
  }catch{tell('受付結果を確認できません。同じ要求で再試行できます。新しい要求は自動送信しません。');}
  finally{submitting=false;e.target.disabled=!initial.request;}
 });
 root.addEventListener('click',async e=>{const a=e.target.closest('a');if(a?.matches('[data-sf-synopsis]')&&!e.ctrlKey&&!e.metaKey){e.preventDefault();remember();if(pending.size&&!await flush())return;await window.WBGeneration.open(a.href,a);return;}if(!a||e.defaultPrevented||e.ctrlKey||e.metaKey)return;remember();if(pending.size){e.preventDefault();if(await flush())location.href=a.href;}});
 root.addEventListener('submit',async e=>{if(!pending.size)return;e.preventDefault();if(await flush())e.target.requestSubmit();});
 window.addEventListener('beforeunload',e=>{remember();if(pending.size){e.preventDefault();e.returnValue='';}});
 try{for(const [id,change] of JSON.parse(sessionStorage.getItem(storageKey+':draft')||'[]'))if(items.has(id))pending.set(id,change);}catch{}
 if(pending.size){failed=true;status('未保存の入力を復元しました。再試行で現在の判定と照合してください。');if($('[data-sf-retry]'))$('[data-sf-retry]').hidden=false;}
 const remembered=savedState(), requested=new URL(location.href).searchParams.get('candidate');
 if(visible.length){select(visible.includes(requested)?requested:visible.includes(remembered.candidate)?remembered.candidate:visible[0]);
  const scroller=$('[data-sf-list]')||$('.sf-grid-scroll');if(scroller)scroller.scrollTop=remembered.scroll||0;
  $$('[data-sf-tab]').find(b=>b.dataset.sfTab===remembered.panel)?.click();
 }else if(initial.view==='list'||initial.view==='grid'){for(const e of $$('[name="sf-verdict"],[data-sf-note],[data-sf-prev],[data-sf-next]'))e.disabled=true;}
 window.addEventListener('wb-generation-entry',e=>{const d=e.detail;if(d.run_id!==initial.run_id)return;const c=items.get(d.candidate_id);if(!c)return;c.synopsis=d.text;c.synopsis_state='あらすじ生成済み';c.can_synopsis=false;c.status.synopsize='ok';c.output_href=`/outputs/${encodeURIComponent(d.output_id)}`;const row=$$('[data-candidate-id]').find(r=>r.dataset.candidateId===d.candidate_id);const snippet=row?.querySelector('.sf-snippet');if(snippet)snippet.textContent=d.text;if(current===d.candidate_id)select(current);});
 footer();
})();
