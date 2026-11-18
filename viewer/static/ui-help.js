/* Shared supplementary help. No form submission, navigation or persistence. */
(function(global){
 'use strict';
 let serial=0,active=null;
 const clamp=(value,min,max)=>Math.min(Math.max(value,min),Math.max(min,max));
 function place(anchor,popup,viewport,gap=8){
  const width=Math.min(popup.width,Math.max(0,viewport.width-gap*2)),height=Math.min(popup.height,Math.max(0,viewport.height-gap*2));
  const below=viewport.height-anchor.bottom-gap,above=anchor.top-gap;
  return {left:clamp(anchor.left,gap,viewport.width-width-gap),top:clamp(below>=height||below>=above?anchor.bottom+gap:anchor.top-height-gap,gap,viewport.height-height-gap),width,height};
 }
 function create(label,text,{theme='',container=null}={}){
  const doc=global.document,button=doc.createElement('button'),glyph=doc.createElement('span'),panel=doc.createElement('div'),head=doc.createElement('div'),title=doc.createElement('strong'),content=doc.createElement('p'),dismiss=doc.createElement('button');
  const id='ui-help-'+(++serial),listeners=[],openListeners=[];
  button.type='button';button.className='ui-help-button';button.dataset.uiHelp='';button.setAttribute('aria-label',label);button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls',id);button.setAttribute('aria-expanded','false');
  glyph.className='ui-help-glyph';glyph.textContent='?';glyph.setAttribute('aria-hidden','true');button.append(glyph);
  panel.id=id;panel.className='ui-help-panel'+(theme?' '+theme:'');panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','false');panel.setAttribute('tabindex','-1');panel.setAttribute('aria-labelledby',id+'-title');panel.setAttribute('aria-describedby',id+'-text');
  head.className='ui-help-head';title.id=id+'-title';title.textContent=label;content.id=id+'-text';content.textContent=text;
  dismiss.type='button';dismiss.className='ui-help-close';dismiss.textContent='×';dismiss.setAttribute('aria-label','説明を閉じる');head.append(title,dismiss);panel.append(head,content);
  let opened=false,pinned=false,overButton=false,overPanel=false,restoring=false,disposed=false,timer;
  const listen=(target,event,fn,list=listeners,capture=false)=>{target.addEventListener(event,fn,capture);list.push(()=>target.removeEventListener?.(event,fn,capture));};
  const clearTimer=()=>{if(timer!==undefined){global.clearTimeout(timer);timer=undefined;}};
  const hasFocus=()=>button.contains(doc.activeElement)||panel.contains(doc.activeElement);
  function position(){
   if(!opened)return;
   if(button.isConnected===false||button.disabled){close(false);return;}
   const viewport={width:doc.documentElement?.clientWidth||global.innerWidth,height:doc.documentElement?.clientHeight||global.innerHeight};
   if(!(viewport.width>0&&viewport.height>0)){close(false);return;}
   const css=global.getComputedStyle?.(panel),gap=parseFloat(css?.getPropertyValue('--ws-help-gap'))||8,limit=parseFloat(css?.getPropertyValue('--ws-help-width'))||360;
   panel.style.width=Math.min(limit,Math.max(0,viewport.width-gap*2))+'px';panel.style.maxHeight=Math.max(0,viewport.height-gap*2)+'px';
   const anchor=button.getBoundingClientRect(),bounds=panel.getBoundingClientRect();
   if(anchor.bottom<0||anchor.top>viewport.height||anchor.right<0||anchor.left>viewport.width){close(false);return;}
   const result=place(anchor,bounds,viewport,gap);panel.style.left=result.left+'px';panel.style.top=result.top+'px';
  }
  function close(restore=false){
   clearTimer();if(!opened)return;opened=false;pinned=false;overPanel=false;panel.hidden=true;panel.remove();button.setAttribute('aria-expanded','false');button.removeAttribute('aria-describedby');
   while(openListeners.length)openListeners.pop()();if(active===api)active=null;
   if(restore&&button.isConnected!==false&&!button.disabled){restoring=true;button.focus({preventScroll:true});restoring=false;}
  }
  function open(focus=false){
   if(disposed||button.disabled)return;clearTimer();if(active&&active!==api)active.close(false);
   if(!opened){opened=true;active=api;panel.hidden=false;(container||doc.body).append(panel);button.setAttribute('aria-expanded','true');button.setAttribute('aria-describedby',content.id);
    listen(doc,'pointerdown',event=>{if(!button.contains(event.target)&&!panel.contains(event.target))close(false);},openListeners,true);
    listen(doc,'focusin',event=>{if(!button.contains(event.target)&&!panel.contains(event.target))close(false);},openListeners,true);
    listen(doc,'keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);}},openListeners,true);
    listen(global,'resize',position,openListeners);listen(doc,'scroll',position,openListeners,true);
   }
   position();if(focus&&opened)panel.focus({preventScroll:true});
  }
  function hideSoon(){clearTimer();timer=global.setTimeout(()=>{timer=undefined;if(!pinned&&!overButton&&!overPanel&&!hasFocus())close(false);},120);}
  listen(button,'pointerenter',event=>{if(event.pointerType==='touch')return;overButton=true;open(false);});
  listen(button,'pointerleave',()=>{overButton=false;hideSoon();});
  listen(button,'focus',()=>{if(!restoring)open(false);});listen(button,'blur',hideSoon);
  listen(button,'click',()=>{if(opened&&pinned)close(true);else{pinned=true;open(true);}});
  listen(panel,'pointerenter',()=>{overPanel=true;clearTimer();});listen(panel,'pointerleave',()=>{overPanel=false;hideSoon();});
  listen(panel,'focusout',hideSoon);listen(dismiss,'click',()=>close(true));
  const api={button,panel,open,close,position,destroy(){close(false);disposed=true;while(listeners.length)listeners.pop()();}};
  return api;
 }
 global.WorldBloomHelp={create,place};
})(window);
