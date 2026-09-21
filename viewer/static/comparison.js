"use strict";
(() => {
 const root=document.querySelector('[data-comparison]');if(!root)return;
 const $=s=>root.querySelector(s),$$=s=>[...root.querySelectorAll(s)];
 const initial=JSON.parse(root.dataset.initial), items=new Map(initial.items.map(c=>[c.candidate_id,c]));
 const cards=new Map($$('[data-cp-card]').map(c=>[c.dataset.cpCard,c]));
 const pending=new Map(), endpoint=`/api/runs/${encodeURIComponent(initial.run_id)}/selection`;
 const draftKey=`comparison:${initial.run_id}:draft`;
 let revision=initial.revision,busy=false,blocked=false,canRetry=false,timer,flight;
 const notify=text=>{$('[data-cp-notice]').textContent=text;$('[data-cp-notice]').hidden=!text;};
 const status=text=>{$('[data-cp-save]').textContent=text;};
 const value=id=>({...items.get(id),...pending.get(id)});
 function stash(){try{sessionStorage.setItem(draftKey,JSON.stringify([...pending]));}catch{}}
 function footer(){
  const all=[...items.keys()].map(value), n=all.filter(c=>c.state==='adopted').length,held=all.filter(c=>c.state==='held').length;
  $('[data-cp-count]').textContent=`採用 ${n}件 · 保留 ${held}件`;
  $('[data-cp-proceed]').textContent=`採用候補を確認（${n}件） →`;
  $('[data-cp-proceed]').disabled=initial.running||busy||blocked||pending.size>0||!n;
  $('[data-cp-check]').hidden=!blocked;$('[data-cp-check]').disabled=busy;
  $('[data-cp-retry]').hidden=!canRetry;$('[data-cp-retry]').disabled=busy;
  $('[data-cp-discard]').hidden=!canRetry;$('[data-cp-discard]').disabled=busy;
 }
 function paint(){
  for(const [id,card] of cards){const c=value(id);for(const r of card.querySelectorAll('[data-cp-state]'))r.checked=r.value===c.state;
   const note=card.querySelector('[data-cp-note]');if(note.value!==c.note)note.value=c.note||'';
  }footer();
 }
 async function api(path,body){
  const response=await fetch(path,{method:body?'POST':'GET',headers:{'X-WorldBloom-Client':'1',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  const data=await response.json();return {ok:response.ok,status:response.status,data};
 }
 function applyRemote(data){revision=data.revision;const entries=new Map(data.entries.map(e=>[e.candidate_id,e]));
  for(const [id,c] of items){const e=entries.get(id);c.state=e?.state||'unclassified';c.note=e?.note||'';}
 }
 function edit(id,change){pending.set(id,{...pending.get(id),...change});stash();status(blocked?'未保存の入力があります':'未保存');footer();}
 function flush(){
  clearTimeout(timer);if(busy)return flight;if(blocked||initial.running)return Promise.resolve(false);
  if(!pending.size)return Promise.resolve(true);
  busy=true;footer();status('保存中…');
  flight=(async()=>{try{
   while(pending.size){
    const [id,change]=pending.entries().next().value,sent={...change};
    const r=await api(endpoint,{expected_revision:revision,changes:[{candidate_id:id,...sent}]});
    if(!r.ok)throw Error(r.status===409?'別の画面で判定が更新されています。入力を保持しました。保存状況を確認してください。':r.data.code==='projection_pending'?'判定は保存されましたが旧表示への反映を確認できません。保存状況を確認してください。':r.data.message||'保存できませんでした。入力を保持しています。');
    applyRemote(r.data);
    const next=pending.get(id);
    for(const k of Object.keys(sent))if(next?.[k]===sent[k])delete next[k];
    if(next&&!Object.keys(next).length)pending.delete(id);
    stash();
   }
   status('保存しました');notify('');paint();return true;
  }catch(e){blocked=true;canRetry=false;status('保存を確認してください');notify(e.message||'通信結果を確認できません。入力は保持しています。');return false;}
  finally{busy=false;footer();}})();return flight;
 }
 for(const [id,card] of cards){
  card.querySelectorAll('[data-cp-state]').forEach(r=>r.addEventListener('change',()=>{edit(id,{state:r.value});flush();}));
  card.querySelector('[data-cp-note]').addEventListener('input',e=>{edit(id,{note:e.target.value});clearTimeout(timer);timer=setTimeout(flush,650);});
  card.querySelector('[data-cp-note]').addEventListener('blur',()=>flush());
 }
 async function checkSaved(){
  if(busy)return;busy=true;footer();
  try{const r=await api(endpoint);if(!r.ok)throw Error(r.data.message||'保存状況を取得できません。');
   applyRemote(r.data);
   for(const [id,change] of pending){for(const key of Object.keys(change))if(items.get(id)?.[key]===change[key])delete change[key];if(!Object.keys(change).length)pending.delete(id);}
   stash();canRetry=!!pending.size;blocked=!!pending.size;
   const descriptions=[...pending.keys()].map(id=>{const remote=items.get(id);return `${cards.get(id)?.getAttribute('aria-label')||id}: 保存済みの判定「${({unclassified:'未分類',adopted:'採用',held:'保留',rejected:'除外'})[remote.state]}」、メモ「${remote.note||'なし'}」`;});
   notify(pending.size?'保存済みの内容と入力が異なります。自分の入力を保存するか、最新の判定を使うか選んでください。 '+descriptions.join(' / '):'保存済みの内容を確認しました。');
   status(pending.size?'未保存の入力を保持しています':'保存済みの内容を確認しました');paint();
  }catch(e){notify(e.message);blocked=true;}finally{busy=false;footer();}
 }
 $('[data-cp-check]').addEventListener('click',checkSaved);
 $('[data-cp-retry]').addEventListener('click',()=>{canRetry=false;blocked=false;flush();});
 $('[data-cp-discard]').addEventListener('click',()=>{pending.clear();stash();canRetry=false;blocked=false;notify('');status('最新の判定を表示しています');paint();});
 async function leave(url){if(busy&&!await flight)return;if(pending.size&&!await flush())return;if(blocked)return;location.href=url;}
 $('[data-cp-proceed]').addEventListener('click',()=>leave(`/selected?run=${encodeURIComponent(initial.run_id)}`));
 root.addEventListener('click',async e=>{
  const a=e.target.closest('a');if(!a||e.defaultPrevented||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;
  e.preventDefault();if(busy&&!await flight)return;if(pending.size&&!await flush()||blocked)return;
  if(a.hasAttribute('data-cp-generate')){await window.WBGeneration.open(a.href,a);return;}location.href=a.href;
 });
 window.addEventListener('beforeunload',e=>{if(pending.size){e.preventDefault();e.returnValue='';}});
 const captions={story:'あらすじは保存された生成文です。各候補の照合状態と原記録を確認できます。',evidence:'原記録から同じ四項目を比較します。「不明」や転機の「候補」は、確認済みの事実と区別します。',data:'記録された世代・品質・原記録の状態です。物語の優劣は判定していません。'};
 captions.evidence+=' '+initial.trajectory_message;
 function tab(key){
  for(const b of $$('[data-cp-tab]')){const active=b.dataset.cpTab===key;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;}
  for(const p of $$('[data-cp-view]'))p.hidden=p.dataset.cpView!==key;
  $('.cp-matrix').dataset.view=key;$('#cp-panel').setAttribute('aria-labelledby',`cp-tab-${key}`);
  $('#cp-panel').scrollTop=0;$('[data-cp-caption]').textContent=captions[key];
 }
 $$('[data-cp-tab]').forEach(b=>{
  b.addEventListener('click',()=>tab(b.dataset.cpTab));
  b.addEventListener('keydown',e=>{const tabs=$$('[data-cp-tab]'),i=tabs.indexOf(b);const n=e.key==='ArrowRight'?(i+1)%tabs.length:e.key==='ArrowLeft'?(i+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:null;
   if(n===null)return;e.preventDefault();tabs[n].click();tabs[n].focus();});
 });
 const dialog=$('.cp-picker'),choices=$$('[data-cp-choice]');
 function pickerCount(){
  const n=choices.filter(c=>c.checked).length;$('[data-cp-picked]').textContent=`${n}件を選択（2〜4件）`;
  for(const c of choices)c.disabled=!c.checked&&n>=4;
  $('[data-cp-apply]').disabled=n<2||n>4;
 }
 $('[data-cp-picker]').addEventListener('click',()=>{
  for(const c of choices)c.checked=initial.bound.includes(c.value);
  $('[data-cp-search]').value='';$$('[data-cp-option]').forEach(c=>c.hidden=false);
  pickerCount();dialog.showModal();
 });
 choices.forEach(c=>c.addEventListener('change',pickerCount));
 $('[data-cp-search]').addEventListener('input',e=>{const q=e.target.value.toLocaleLowerCase();for(const option of $$('[data-cp-option]'))option.hidden=!option.textContent.toLocaleLowerCase().includes(q);});
 async function compare(ids){
  if(busy&&!await flight){dialog.close();return;}if(pending.size&&!await flush()||blocked){dialog.close();return;}
  try{
   const r=await api(`/api/runs/${encodeURIComponent(initial.run_id)}/candidates`);
   if(!r.ok||r.data.revision!==initial.publication)throw Error('公開版が更新されています。一覧に戻り、比較対象を選び直してください。');
   const qs=new URLSearchParams();
   for(const id of ids){const c=choices.find(c=>c.value===id);if(!c)throw Error('比較対象を確認できません。');qs.append('cell',c.dataset.cell);qs.append('candidate',id);}
   qs.set('publication',initial.publication);location.href=initial.base+'?'+qs;
  }catch(e){dialog.close();notify(e.message);}
 }
 $('[data-cp-apply]').addEventListener('click',()=>{const ids=choices.filter(c=>c.checked).map(c=>c.value);if(ids.length>=2&&ids.length<=4)compare(ids);});
 $$('[data-cp-remove]').forEach(b=>b.addEventListener('click',()=>compare(initial.bound.filter(id=>id!==b.dataset.cpRemove))));
 window.addEventListener('wb-generation-entry',e=>{
  if(e.detail.run_id!==initial.run_id)return;const card=cards.get(e.detail.candidate_id);if(!card)return;
  card.querySelector('[data-cp-story]').textContent=e.detail.text;card.querySelector('[data-cp-reader]').textContent='AI生成・未照合';card.querySelector('[data-cp-generate]')?.remove();
 });
 try{for(const [id,change] of JSON.parse(sessionStorage.getItem(draftKey)||'[]'))if(items.has(id))pending.set(id,change);}catch{}
 if(pending.size){blocked=true;status('未保存の入力を復元しました');notify('未保存の入力を保持しています。「保存状況を確認」で最新の判定と照合してください。');}
 paint();
})();
