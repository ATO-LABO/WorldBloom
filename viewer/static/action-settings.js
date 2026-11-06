/* Navigation and local filtering only. Saving stays with the existing editors. */
(() => {
  'use strict';
  const root=document.querySelector('[data-action-settings]');
  if(!root)return;
  const tabs=[...root.querySelectorAll('[data-as-tab]')];
  const panels=[...root.querySelectorAll('[data-as-panel]')];
  const scrolls=new Map(),body=root.querySelector('.as-body');
  const frame=root.querySelector('.as-settings-frame'),helpHost=root.querySelector('[data-as-help]'),frameScrolls=new Map();
  const layout=frame&&window.WorldBloomSettingsFrame?.mount(frame,{header:frame.querySelector('.ui-settings-header'),tabs:frame.querySelector('[role="tablist"]'),body});
  const helper=helpHost&&window.WorldBloomHelp?.create('行動設定の説明',helpHost.dataset.asHelpText,{theme:'ui-settings-theme'});
  if(helper)helpHost.replaceChildren(helper.button);
  const typeHelpers=[...root.querySelectorAll('[data-as-type-help]')].map(host=>{
    const help=window.WorldBloomHelp?.create(host.dataset.asHelpName,host.dataset.asHelpText,{theme:'ui-settings-theme'});
    if(help)host.replaceChildren(help.button);return help;
  }).filter(Boolean);
  window.WorldBloomActionPresentation={sync(state){if(helper){helper.button.disabled=state.busy;if(state.busy)helper.close();}typeHelpers.forEach(h=>{h.button.disabled=state.busy;if(state.busy)h.close();});}};
  addEventListener('pagehide',event=>{if(event.persisted)return;layout?.disconnect();helper?.destroy();typeHelpers.forEach(h=>h.destroy());});
  let selected='types';
  function select(key,update=false){
    if(key==='mechanics'||key==='genres')key='types';if(['people','time'].includes(key))key='actions';
    if(!tabs.some(t=>t.dataset.asTab===key))key='types';
    scrolls.set(selected,body.scrollTop);if(frame)frameScrolls.set(selected,frame.scrollTop);selected=key;
    tabs.forEach(t=>{const active=t.dataset.asTab===key;t.setAttribute('aria-selected',String(active));t.tabIndex=active?0:-1;});
    panels.forEach(p=>p.hidden=p.dataset.asPanel!==key);
    body.scrollTop=scrolls.get(key)||0;
    if(frame)frame.scrollTop=frameScrolls.get(key)||0;
    if(update)history.pushState(null,'','#'+new URLSearchParams({tab:key}));
  }
  tabs.forEach(tab=>tab.addEventListener('click',()=>select(tab.dataset.asTab,true)));
  addEventListener('hashchange',()=>select(new URLSearchParams(location.hash.slice(1)).get('tab')));
  addEventListener('popstate',()=>select(new URLSearchParams(location.hash.slice(1)).get('tab')));
  const rows=[...root.querySelectorAll('[data-as-row]')], search=root.querySelector('[data-as-search]'),genre=root.querySelector('[data-as-genre-filter]');
  function groups(){
    if(!rows.length||!rows.every(row=>row.dataset.asCategoryLabel))return;
    root.querySelectorAll('[data-as-group]').forEach(header=>header.remove());
    const parent=rows[0].parentElement;let previous='';
    [...parent.children].filter(row=>row.hasAttribute('data-as-row')&&!row.hidden).forEach(row=>{
      const key=row.dataset.asGenre+':'+row.dataset.asDisabled;if(key===previous)return;previous=key;
      const heading=document.createElement('tr');heading.className='as-category-heading';heading.dataset.asGroup=row.dataset.asGenre;heading.dataset.asGroupDisabled=row.dataset.asDisabled;
      const cell=document.createElement('th'),title=document.createElement('h2');cell.setAttribute('colspan','5');title.textContent=row.dataset.asCategoryLabel+(row.dataset.asDisabled==='true'?'（無効）':'');cell.append(title);heading.append(cell);parent.insertBefore(heading,row);
    });
  }
  window.WorldBloomActionGroups={refresh:groups};
  function filter(){
    const query=search.value.trim().toLocaleLowerCase(),selectedGenre=genre?.value||'all';let count=0;
    rows.forEach(row=>{row.hidden=!row.dataset.asSearchText.toLocaleLowerCase().includes(query)||(selectedGenre!=='all'&&row.dataset.asGenre!==selectedGenre);if(!row.hidden)count++;});
    root.querySelector('[data-as-count]').textContent=count+' / '+rows.length+'件';
    root.querySelector('[data-as-empty]').hidden=count!==0;
    groups();
  }
  search.addEventListener('input',filter);
  genre?.addEventListener('change',filter);
  select(new URLSearchParams(location.hash.slice(1)).get('tab'));
  window.WorldBloomTabs?.enhance(root.querySelector('[role="tablist"]'));
})();
