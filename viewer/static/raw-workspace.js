"use strict";
(() => {
 const root=document.querySelector('[data-record-viewer]'); if(!root)return;
 const $=s=>root.querySelector(s), $$=s=>[...root.querySelectorAll(s)];
 const original=$('[data-rv-original]'), status=$('[data-rv-status]');
 const copySource=$('[data-rv-copy-source]');
 const originalText=copySource?JSON.parse(copySource.textContent):(original?.textContent??'');
 if(original){
  const url=new URL(location.href);url.searchParams.set('source',root.dataset.source);url.searchParams.set('line',root.dataset.line);
  history.replaceState(null,'',url);
 }
 function mode(key){
  for(const a of $$('[data-rv-tab]')){const active=a.dataset.rvTab===key;a.setAttribute('aria-selected',String(active));a.tabIndex=active?0:-1;}
  for(const p of $$('[role=tabpanel]'))p.hidden=p.id!=='rv-panel-'+key;
  const url=new URL(location.href);url.searchParams.set('mode',key);history.replaceState(null,'',url);
  for(const a of $$('[data-rv-link]')){const next=new URL(a.href);next.searchParams.set('mode',key);a.href=next.href;}
  for(const input of $$('input[name=mode]'))input.value=key;
  $('.rv-detail-scroll').scrollTop=0;
 }
 $$('[data-rv-tab]').forEach(a=>{
  a.addEventListener('click',e=>{e.preventDefault();mode(a.dataset.rvTab);});
  a.addEventListener('keydown',e=>{
   const tabs=$$('[data-rv-tab]'),i=tabs.indexOf(a);
   const next=e.key==='ArrowRight'?(i+1)%tabs.length:e.key==='ArrowLeft'?(i+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:null;
   if(next===null)return;e.preventDefault();tabs[next].click();tabs[next].focus();
  });
 });
 $('[data-rv-wrap]')?.addEventListener('change',e=>$('.rv-json').classList.toggle('is-wrapped',e.target.checked));
 function fallback(text,button){
  const dialog=document.createElement('dialog');dialog.className='rv-copy-dialog';
  const title=document.createElement('h2');title.textContent='コピーする内容';
  const hint=document.createElement('p');hint.textContent='自動コピーが許可されませんでした。選択された内容を Ctrl+C などでコピーしてください。';
  const area=document.createElement('textarea');area.readOnly=true;area.value=text;area.setAttribute('aria-label','コピーする内容');
  const close=document.createElement('button');close.textContent='閉じる';close.addEventListener('click',()=>dialog.close());
  dialog.append(title,hint,area,close);root.append(dialog);
  dialog.addEventListener('close',()=>{dialog.remove();button.focus();},{once:true});
  dialog.showModal();area.focus();area.select();
 }
 $$('[data-rv-copy]').forEach(button=>button.addEventListener('click',async()=>{
  let text=originalText;
  if(button.dataset.rvCopy==='link'){
   const url=new URL(location.href);url.searchParams.set('line',root.dataset.line);url.searchParams.set('source',root.dataset.source);url.hash='L'+root.dataset.line;text=url.href;
  }
  try{await navigator.clipboard.writeText(text);status.textContent=button.dataset.rvCopy==='link'?'この行へのリンクをコピーしました。':'この行の原文をコピーしました。';}
  catch{status.textContent='自動コピーできませんでした。';fallback(text,button);}
 }));
 const selected=$('.rv-records [aria-current]');
 if(selected){
  const list=$('.rv-records'),r=selected.getBoundingClientRect(),b=list.getBoundingClientRect();
  list.scrollTop+=r.top-b.top-list.clientHeight/2+r.height/2;
 }
})();
