(() => {
 'use strict';
 function init(root){
  const picker=root.querySelector('[data-plan-group]'),groups=[...root.querySelectorAll('[data-plan-group-panel]')];
  const find=(group,index)=>group.querySelector(`[data-plan-detail="${index}"]`);
  function reveal(group,button){
   const track=group.querySelector('.pw-events');
   if(!button||!track||track.clientWidth===0||track.scrollWidth<=track.clientWidth)return;
   const b=button.getBoundingClientRect(),t=track.getBoundingClientRect();
   if(b.left<t.left||b.right>t.right)track.scrollLeft+=b.left-t.left-(track.clientWidth-b.width)/2;
  }
  function select(group,button){
   for(const b of group.querySelectorAll('[data-plan-event]'))b.setAttribute('aria-pressed',String(b===button));
   for(const panel of group.querySelectorAll('[data-plan-detail]'))panel.hidden=panel!==find(group,button.dataset.planEvent);
   reveal(group,button);
  }
  picker.addEventListener('change',()=>{for(const group of groups)group.hidden=group.dataset.planGroupPanel!==picker.value;});
  for(const group of groups){
   const buttons=[...group.querySelectorAll('[data-plan-event]')],all=group.querySelector('[data-plan-all]');
   if(typeof ResizeObserver!=='undefined'){
    const track=group.querySelector('.pw-events');
    if(track)new ResizeObserver(()=>reveal(group,buttons.find(b=>b.getAttribute('aria-pressed')==='true'))).observe(track);
   }
   all.addEventListener('click',()=>{
    const expanded=all.getAttribute('aria-pressed')!=='true';all.setAttribute('aria-pressed',String(expanded));all.textContent=expanded?'変化のある場面だけ表示':'全行動を表示';
    const selected=buttons.find(b=>b.getAttribute('aria-pressed')==='true');
    if(!expanded&&selected?.hasAttribute('data-plan-routine'))select(group,buttons.find(b=>!b.hasAttribute('data-plan-routine')));
    for(const b of buttons)if(b.hasAttribute('data-plan-routine'))b.hidden=!expanded;
   });
   buttons.forEach(b=>{
    b.addEventListener('click',()=>select(group,b));
    b.addEventListener('keydown',event=>{
     const visible=buttons.filter(x=>!x.hidden),i=visible.indexOf(b);
     const target=event.key==='ArrowDown'||event.key==='ArrowRight'?visible[(i+1)%visible.length]:
      event.key==='ArrowUp'||event.key==='ArrowLeft'?visible[(i+visible.length-1)%visible.length]:
      event.key==='Home'?visible[0]:event.key==='End'?visible[visible.length-1]:null;
     if(target){event.preventDefault();select(group,target);target.focus();}
    });
   });
  }
  root.dataset.planningReady='true';
 }
 if(typeof module!=='undefined')module.exports={init};
 if(typeof document!=='undefined')document.querySelectorAll('[data-planning-workspace]').forEach(init);
})();
