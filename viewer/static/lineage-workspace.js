"use strict";
(() => {
 const root=document.querySelector('[data-lineage]');if(!root)return;
 const $=s=>root.querySelector(s),$$=s=>[...root.querySelectorAll(s)];
 const track=$('.lineage-band'),nodes=$$('[data-lw-node]');
 function remember(key,value){
  const url=new URL(location.href);url.searchParams.set(key,value);history.replaceState(null,'',url);
  for(const a of $$('[data-lw-point]')){const next=new URL(a.href);next.searchParams.set(key,value);a.href=next.href;}
 }
 function changeView(mode){
  if(!track)return;
  for(const n of nodes)n.hidden=mode==='all'?false:mode==='fit'?n.dataset.fit!=='1':n.dataset.key!=='1';
  track.dataset.view=mode;
  for(const b of $$('[data-lw-view]'))b.setAttribute('aria-pressed',String(b.dataset.lwView===mode));
  const shown=nodes.filter(n=>!n.hidden).length;
  $('[data-lw-track-note]').textContent=shown<nodes.length?`${nodes.length}地点中${shown}地点を表示 · 途中の地点は省略`:'この候補につながる主系の全地点を表示しています。';
  remember('view',mode);
  $('.lw-track-scroll').scrollLeft=0;
 }
 $$('[data-lw-view]').forEach(b=>b.addEventListener('click',()=>changeView(b.dataset.lwView)));
 function selectTab(key){
  for(const b of $$('[data-lw-tab]')){const active=b.dataset.lwTab===key;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;}
  for(const p of $$('[data-lw-panel]'))p.hidden=p.dataset.lwPanel!==key;
  remember('tab',key);$('.lw-detail-scroll').scrollTop=0;
 }
 $$('[data-lw-tab]').forEach(b=>{
  b.addEventListener('click',()=>selectTab(b.dataset.lwTab));
  b.addEventListener('keydown',e=>{const tabs=$$('[data-lw-tab]'),i=tabs.indexOf(b);const next=e.key==='ArrowRight'?(i+1)%tabs.length:e.key==='ArrowLeft'?(i+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:null;if(next===null)return;e.preventDefault();tabs[next].click();tabs[next].focus();});
 });
 if(track){
  changeView(track.dataset.view);
  const active=$('.lineage-node a[aria-current]');if(active){const box=active.closest('li'),scroller=$('.lw-track-scroll');scroller.scrollLeft=Math.max(0,box.offsetLeft-scroller.offsetLeft-scroller.clientWidth/2+box.clientWidth/2);}
  const current=$('.lw-event-list [aria-current]');if(current){const list=$('.lw-event-list');if(list.scrollHeight>list.clientHeight)list.scrollTop=Math.max(0,current.offsetTop-list.offsetTop-list.clientHeight/2);}
 }
})();
