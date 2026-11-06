(function(global){'use strict';
const norm=v=>String(v||'').normalize('NFKC').toLocaleLowerCase().trim();
function mount(host,onChange){
 const source=host.querySelector('[data-cw-source]'),search=host.querySelector('[data-cw-world-search]'),count=host.querySelector('[data-cw-world-count]'),empty=host.querySelector('[data-cw-world-empty]'),reset=host.querySelector('[data-cw-world-reset]'),picked=host.ownerDocument.querySelector('[data-cw-world-picked]');
 const cards=[...host.querySelectorAll('[data-cw-world-card]')],byId=new Map(cards.map(c=>[c.dataset.worldId,c]));
 if(!byId.has(source.value))source.value='';
 function sync(){
  const query=norm(search.value);let visible=0;
  for(const card of cards){const selected=card.dataset.worldId===source.value;card.hidden=!norm(card.dataset.search).includes(query);if(!card.hidden)visible++;card.dataset.selected=String(selected);card.querySelector('[data-cw-world-option]').checked=selected;card.querySelector('[data-cw-world-label]').textContent=selected?'選択中':'この世界を選ぶ';}
  count.textContent=query?visible+' / '+cards.length+'件':cards.length+'件';empty.hidden=visible>0||!cards.length;reset.hidden=!query;
  const selected=byId.get(source.value);picked.textContent=selected?'選択中：'+(selected.dataset.worldName.length>50?selected.dataset.worldName.slice(0,50)+'…':selected.dataset.worldName)+'（ID: '+selected.dataset.worldId+'）'+(selected.hidden?'（検索結果の外）':'')+'／元の世界は変更しません':'元の世界を選択してください';picked.title=selected?selected.dataset.worldName:'';
 }
 function choose(id,focus=false){const card=byId.get(id);if(!card||source.disabled)return false;source.value=id;sync();if(focus)card.querySelector('[data-cw-world-option]').focus();onChange();return true;}
 for(const card of cards){const radio=card.querySelector('[data-cw-world-option]');radio.addEventListener('change',()=>{if(radio.checked)choose(radio.value);});card.addEventListener('click',event=>{if(event.target.closest('input,label,button,a,select,textarea,[tabindex]'))return;choose(card.dataset.worldId,true);});}
 search.addEventListener('input',sync);reset.addEventListener('click',()=>{search.value='';sync();search.focus();});sync();
 return {sync,choose,selected:()=>source.value};
}
const api={norm,mount};if(typeof module==='object'&&module.exports)module.exports=api;global.WorldBloomWorldPicker=api;
})(typeof window==='object'?window:globalThis);
