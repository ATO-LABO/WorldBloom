/* Opt-in settings frame: geometry only; the owning form keeps save/navigation. */
(function(global){
 'use strict';
 function scrollMode(height,header,tabs,minimum){return height>0&&height-header-tabs<minimum?'flow':'fixed';}
 function mount(frame,{header,tabs,body}){
  let observer=null,closed=false;
  const refresh=()=>{
   if(closed)return;
   if(!frame.isConnected){disconnect();return;}
   const minimum=parseFloat(global.getComputedStyle(frame).getPropertyValue('--ws-settings-body-min'))||160;
   frame.dataset.settingsScroll=scrollMode(frame.clientHeight,header.getBoundingClientRect().height,(tabs?.getBoundingClientRect().height||0),minimum);
  };
  function disconnect(){closed=true;observer?.disconnect();}
  frame.dataset.settingsScroll='fixed';
  if(global.ResizeObserver){observer=new global.ResizeObserver(refresh);[frame,header,tabs].filter(Boolean).forEach(element=>observer.observe(element));refresh();}
  return {refresh,disconnect};
 }
 global.WorldBloomSettingsFrame={mount,scrollMode};
})(window);
