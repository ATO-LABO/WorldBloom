/* Explanatory help only. No authoring input, request or selection mutation. */
(()=>{'use strict';
const root=document.querySelector('[data-creation-workflow]'),button=root?.querySelector('[data-cw-help]'),text=root?.querySelector('[data-cw-help-text]');if(!button||!text)return;
const wrap=button.parentElement;let pinned=false;
const position=()=>{if(text.hidden)return;const edge=16,r=button.getBoundingClientRect(),w=text.offsetWidth,h=text.offsetHeight;const left=Math.max(edge,Math.min(innerWidth-w-edge,r.right-w));const top=r.bottom+h+edge<=innerHeight?r.bottom:Math.max(edge,r.top-h);text.style.left=left+'px';text.style.top=top+'px';};
const show=value=>{text.hidden=!value;button.setAttribute('aria-expanded',String(value));if(value)position();};
wrap.addEventListener('pointerenter',e=>{if(e.pointerType==='touch')return;show(true);});
wrap.addEventListener('pointerleave',()=>{if(!pinned&&!wrap.contains(document.activeElement))show(false);});
button.addEventListener('focus',()=>show(true));
wrap.addEventListener('focusout',e=>{if(!wrap.contains(e.relatedTarget)&&!pinned)show(false);});
button.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();pinned=!pinned;show(pinned);});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!text.hidden){pinned=false;show(false);e.stopPropagation();}});
document.addEventListener('pointerdown',e=>{if(!wrap.contains(e.target)){pinned=false;show(false);}});
window.addEventListener('resize',position);window.addEventListener('scroll',position,true);
})();
