/* Shared appearance/keyboard contract; each workspace owns selection and guards. */
(function(){
 'use strict';
 const selector='.ui-tabs',buttonSelector=':scope>button',eligible=b=>!b.hidden&&!b.disabled&&getComputedStyle(b).display!=='none';
 const set=(e,k,v)=>{if(e.getAttribute(k)!==v)e.setAttribute(k,v)};
 const reveal=button=>{if(!button)return;const bar=button.parentElement,outer=bar.getBoundingClientRect(),rect=button.getBoundingClientRect();if(!outer.width||!rect.width)return;if(rect.left<outer.left)bar.scrollLeft-=outer.left-rect.left;else if(rect.right>outer.right)bar.scrollLeft+=rect.right-outer.right;};
 function sync(bar){
  const buttons=[...bar.querySelectorAll(buttonSelector)];
  if(!buttons.length||bar.querySelector(':scope>a')){reveal(bar.querySelector(':scope>a[aria-current="page"]'));return;}
  set(bar,'role','tablist');
  let selected=buttons.find(b=>!b.hidden&&getComputedStyle(b).display!=='none'&&(b.hasAttribute('aria-pressed')?b.getAttribute('aria-pressed')==='true':b.hasAttribute('aria-current')?!['false',''].includes(b.getAttribute('aria-current')):b.getAttribute('aria-selected')==='true'));
  if(!selected)selected=buttons.find(eligible);
  for(const b of buttons){set(b,'role','tab');set(b,'aria-selected',String(b===selected));b.tabIndex=b===selected?0:-1;}
  reveal(selected);
 }
 function scan(root){if(root.nodeType!==1&&root!==document)return;if(root.matches?.(selector))sync(root);for(const bar of root.querySelectorAll?.(selector)||[])sync(bar);}
 document.addEventListener('keydown',event=>{
  const button=event.target.closest('.ui-tabs>button,.ui-tabs>a');if(!button||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  const bar=button.parentElement,entries=[...bar.querySelectorAll(':scope>button,:scope>a')].filter(eligible),index=entries.indexOf(button);if(index<0||!entries.length)return;
  event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?entries.length-1:event.key==='ArrowRight'?(index+1)%entries.length:(index+entries.length-1)%entries.length,target=entries[next];
  if(target.tagName==='A'){target.focus({preventScroll:true});reveal(target);return;}
  target.click();sync(bar);
  const current=bar.isConnected?bar.querySelector(':scope>[aria-selected="true"]'):document.activeElement?.closest(selector)?.querySelector(':scope>[aria-selected="true"]');
  if(current?.isConnected){current.focus({preventScroll:true});reveal(current);}
 },true);
 const observer=new MutationObserver(records=>{const bars=new Set();for(const record of records){const parent=record.target.closest?.(selector);if(parent)bars.add(parent);for(const added of record.addedNodes||[])scan(added);}for(const bar of bars)if(bar.isConnected)sync(bar);});
 const start=()=>{scan(document);observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-selected','aria-current','aria-pressed','hidden','disabled']});};
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
 let resizeFrame;window.addEventListener('resize',()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>scan(document));});
 window.WorldBloomTabs={enhance:sync};
})();
