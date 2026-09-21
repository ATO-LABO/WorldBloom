(()=>{'use strict';
const root=document.querySelector('[data-home-library]');if(!root)return;
const tabs=[...root.querySelectorAll('[data-home-tab]')],search=root.querySelector('[data-home-search]'),genre=root.querySelector('[data-home-genre]');
const count=root.querySelector('[data-home-count]'),empty=root.querySelector('[data-home-empty]'),reset=root.querySelector('[data-home-reset]');
let selected='worlds';
const norm=value=>value.normalize('NFKC').toLocaleLowerCase().trim();
function update(){
 const term=norm(search.value),filter=selected==='worlds'?genre.value:'';
 let total=0;
 root.querySelectorAll('#'+selected+' [data-home-card]').forEach(card=>{
  const matched=norm(card.dataset.search).includes(term)&&(!filter||(card.dataset.genre||'__unset__')===filter);
  card.hidden=!matched;if(matched)total++;
 });
 count.textContent=total+'件';
 empty.hidden=!(total===0&&(term||filter));
 const url=new URL(location.href);url.searchParams.delete('q');url.searchParams.delete('genre');url.searchParams.delete('view');
 if(search.value)url.searchParams.set('q',search.value);
 if(genre.value)url.searchParams.set('genre',genre.value);
 if(selected==='genres')url.searchParams.set('view','genres');
 history.replaceState(null,'',url.pathname+url.search);
}
function choose(name,focus=false){
 selected=name;
 tabs.forEach(tab=>{const active=tab.dataset.homeTab===name;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;root.querySelector('#'+tab.dataset.homeTab).hidden=!active;if(active&&focus)tab.focus();});
 root.querySelector('[data-home-genre-label]').hidden=name==='genres';
 search.placeholder=name==='worlds'?'世界名・人物で検索':'ジャンル名・説明で検索';
 search.previousElementSibling.textContent=search.placeholder;
 update();
}
tabs.forEach((tab,i)=>{
 tab.addEventListener('click',()=>choose(tab.dataset.homeTab));
 tab.addEventListener('keydown',e=>{let next;if(e.key==='ArrowRight')next=(i+1)%tabs.length;if(e.key==='ArrowLeft')next=(i+tabs.length-1)%tabs.length;if(e.key==='Home')next=0;if(e.key==='End')next=tabs.length-1;if(next!==undefined){e.preventDefault();choose(tabs[next].dataset.homeTab,true);}});
});
search.addEventListener('input',update);genre.addEventListener('change',update);
reset.addEventListener('click',()=>{search.value='';genre.value='';update();search.focus();});
const query=new URL(location.href).searchParams;search.value=query.get('q')||'';genre.value=query.get('genre')||'';
choose(query.get('view')==='genres'?'genres':'worlds');
})();