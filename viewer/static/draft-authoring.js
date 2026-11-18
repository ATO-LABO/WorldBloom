((global)=>{'use strict';
const fields=['sign','condition','target','analyst','protagonist'];
global.WorldBloomAuthoring={connect(root){
 const select=root.querySelector('[data-wd-story-type]');if(!select)return null;
 const panel=root.querySelector('[data-wd-story-notes]'),notice=root.querySelector('[data-wd-authoring-status]');
 let retained={version:1,type:'',outline:{}};
 const update=()=>panel.hidden=!select.value;
 function set(value){retained=value?structuredClone(value):{version:1,type:'',outline:{}};
  if(retained.type&&![...select.options].some(o=>o.value===retained.type)){const o=document.createElement('option');o.value=retained.type;o.textContent=retained.type+'（保存された型を保持）';select.append(o);}
  select.value=retained.type;for(const key of fields)root.querySelector('[data-wd-story-field="'+key+'"]').value=retained.outline?.[key]||'';update();
 }
 function get(){const value={version:1,type:select.value,outline:{...(retained.outline||{})}};for(const key of fields)value.outline[key]=root.querySelector('[data-wd-story-field="'+key+'"]').value;return value.type||Object.values(value.outline).some(v=>v.length)?value:null;}
 select.addEventListener('change',update);
 root.querySelector('[data-wd-import-authoring]').addEventListener('click',()=>{
  try{const prior=global.WorldBloomCreation.read(global.localStorage);const current=get();if(current&&!global.confirm('現在の制作メモを、以前の導入メモで置き換えますか？'))return;set({version:1,type:prior.type,outline:prior.outline});notice.textContent='以前のメモを入力へ取り込みました。元のブラウザー保存は変更していません。下書き保存でサーバーへ保持できます。';}
  catch(e){notice.textContent='以前のメモを読み込めません。現在の入力と保存済みメモは変更していません。';}
 });
 update();return {get,set,setDisabled(value){for(const el of root.querySelectorAll('[data-wd-authoring] input,[data-wd-authoring] textarea,[data-wd-authoring] select,[data-wd-authoring] button'))el.disabled=value;}};
}};
})(window);
