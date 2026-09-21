/* Read-only navigation never sends a write. Candidate decisions use the revisioned ledger. */
(() => {
 'use strict';
 const shell=document.querySelector('.ux-shell'),header=document.querySelector('.site-header');
 if(shell&&header)new ResizeObserver(()=>shell.style.setProperty('--ux-header',header.getBoundingClientRect().height+'px')).observe(header);
 const reading=document.querySelector('[data-reading]');
 if(reading){
  const tabs=[...reading.querySelectorAll('[data-ux-tab]')];
  function select(button){for(const b of tabs){const active=b===button;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;}for(const p of reading.querySelectorAll('[data-ux-panel]'))p.hidden=p.dataset.uxPanel!==button.dataset.uxTab;const sc=reading.querySelector('.ux-scroll');if(sc)sc.scrollTop=0;}
  tabs.forEach((b,i)=>{b.addEventListener('click',()=>select(b));b.addEventListener('keydown',e=>{const n=e.key==='ArrowRight'?(i+1)%tabs.length:e.key==='ArrowLeft'?(i+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:null;if(n!==null){e.preventDefault();select(tabs[n]);tabs[n].focus();}});});
 }
 const grid=document.querySelector('[data-readonly-grid]');
 if(grid){
  const buttons=[...grid.querySelectorAll('[data-preview]')],panels=[...grid.querySelectorAll('[data-preview-panel]')];
  buttons.forEach(b=>b.addEventListener('click',()=>{for(const other of buttons)other.setAttribute('aria-pressed',String(other===b));for(const p of panels)p.hidden=p.dataset.previewPanel!==b.dataset.preview;}));
  const choices=[...grid.querySelectorAll('input[name=cell]')],form=grid.querySelector('form');
  function count(){const n=choices.filter(c=>c.checked).length;grid.querySelector('[data-compare-count]').textContent=`比較対象 ${n}件（2〜4件）`;form.querySelector('button').disabled=n<2||n>4;for(const c of choices)c.disabled=!c.checked&&n>=4;}
  choices.forEach(c=>c.addEventListener('change',count));count();form.addEventListener('submit',e=>{if(choices.filter(c=>c.checked).length<2)e.preventDefault();});
  grid.querySelector('[data-grid-search]').addEventListener('input',e=>{const q=e.target.value.trim().toLocaleLowerCase();buttons.forEach(b=>{b.closest('td').hidden=!panels.find(p=>p.dataset.previewPanel===b.dataset.preview).textContent.toLocaleLowerCase().includes(q);});});
  grid.querySelector('[data-grid-mode]').addEventListener('click',e=>{const list=grid.querySelector('.ux-grid').classList.toggle('is-list');e.currentTarget.setAttribute('aria-pressed',String(list));e.currentTarget.textContent=list?'格子で表示':'一覧で表示';});
 }
 const river=document.querySelector('[data-river-workspace]');
 if(river){const svg=river.querySelector('svg'),wrap=river.querySelector('.river-wrap');let zoom=1;
  function fit(){svg.style.width=zoom===1?'100%':`${wrap.clientWidth*zoom}px`;svg.style.height=zoom===1?'100%':`${wrap.clientHeight*zoom}px`;river.querySelector('[data-zoom-level]').textContent=zoom===1?'全体':`${Math.round(zoom*100)}%`;}
  river.querySelectorAll('[data-zoom]').forEach(b=>b.addEventListener('click',()=>{zoom=b.dataset.zoom==='fit'?1:Math.min(8,Math.max(1,zoom*(b.dataset.zoom==='in'?1.4:1/1.4)));fit();}));
  river.querySelector('[data-river-only]').addEventListener('change',e=>wrap.classList.toggle('only-selected',e.target.checked));new ResizeObserver(fit).observe(wrap);fit();
 }
 const root=document.querySelector('[data-candidate-binding]');
 if(!root)return;
 const initial=JSON.parse(root.dataset.candidateBinding),note=root.querySelector('[data-ux-note]'),radios=[...root.querySelectorAll('input[name=verdict]')],status=root.querySelector('[data-ux-save]'),reload=root.querySelector('[data-ux-reload]');
 let revision=initial.revision,busy=false,blocked=false,dirty=false,timer,flight;
 const value=()=>({candidate_id:initial.candidate_id,state:radios.find(r=>r.checked).value,note:note.value});
 async function flush(){clearTimeout(timer);if(busy)return flight;if(!dirty)return true;if(blocked||initial.running)return false;busy=true;status.textContent='保存中…';
  flight=(async()=>{try{while(dirty){const sent=value();const response=await fetch(`/api/runs/${encodeURIComponent(initial.run_id)}/selection`,{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify({expected_revision:revision,changes:[sent]})});const result=await response.json();if(!response.ok)throw Error(result.message||'別の画面で変更された可能性があります。保存状況を確認してください。');revision=result.revision;dirty=JSON.stringify(sent)!==JSON.stringify(value());}status.textContent='判定・メモを保存しました';return true;}catch(error){blocked=true;reload.hidden=false;status.textContent=error.message||'保存結果を確認できません。入力を残しています。再送せず保存状況を確認してください。';return false;}finally{busy=false;}})();return flight;
 }
 function edit(){dirty=true;status.textContent=blocked?'未保存の入力があります':'未保存';clearTimeout(timer);if(!blocked)timer=setTimeout(flush,650);}
 radios.forEach(r=>r.addEventListener('change',edit));note.addEventListener('input',edit);
 reload.addEventListener('click',async()=>{reload.disabled=true;try{const response=await fetch(`/api/runs/${encodeURIComponent(initial.run_id)}/selection`);if(!response.ok)throw Error();const result=await response.json(),remote=result.entries.find(c=>c.candidate_id===initial.candidate_id)||{state:'unclassified',note:''};revision=result.revision;if(remote.state===value().state&&remote.note===value().note){dirty=false;blocked=false;reload.hidden=true;status.textContent='入力した内容が保存されていることを確認しました';}else{status.textContent='保存済みの判定は「'+({unclassified:'未分類',adopted:'採用',held:'保留',rejected:'除外'}[remote.state]||remote.state)+'」です。入力を控えてから再読み込みし、最新の内容を確認してください。';}}catch{status.textContent='保存状況を確認できませんでした。入力は残っています。';}finally{reload.disabled=false;}});
 root.addEventListener('click',async e=>{const a=e.target.closest('a');if(!a||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;if(!dirty&&!busy)return;e.preventDefault();if(await flush())location.href=a.href;});
 addEventListener('beforeunload',e=>{if(dirty||busy){e.preventDefault();e.returnValue='';}});
})();
