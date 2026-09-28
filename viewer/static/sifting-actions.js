"use strict";
(() => {
 const root=document.querySelector('[data-sifting]'), panel=root?.querySelector('[data-sf-panel="actions"]');if(!panel)return;
 const initial=JSON.parse(root.dataset.initial||'{}'), $=s=>panel.querySelector(s);
 const filter=$('[data-sa-actor]'), scroll=$('[data-sa-scroll]'), body=$('[data-sa-rows]'), detail=$('[data-sa-detail]'), message=$('[data-sa-message]');
 const key=`sf-actions:${initial.run_id}:${initial.publication}`, states=new Map(), details=new Map();
 let current=null, loaded=null, ticket=0, detailTicket=0, active=false;
 try{for(const [id,state] of JSON.parse(sessionStorage.getItem(key)||'[]'))states.set(id,state);}catch{}
 const state=()=>{if(!states.has(current.candidate_id))states.set(current.candidate_id,{actor:null,filters:{}});return states.get(current.candidate_id);};
 const position=()=>{const s=state(), name=`actor:${s.actor}`;if(!Object.hasOwn(s.filters,name))s.filters[name]={line:null,scroll:0};return s.filters[name];};
 function remember(){if(!current||!loaded||!active)return;position().scroll=scroll.scrollTop;try{sessionStorage.setItem(key,JSON.stringify([...states]));}catch{}}
 function say(text){message.textContent=text;message.hidden=!text;}
 function clearDetail(){detail.replaceChildren();const p=document.createElement('p');p.textContent='行動を選ぶと詳細を表示します。';detail.append(p);panel.classList.remove('sa-show-detail');}
 function address(line){const q=new URLSearchParams({publication:String(initial.publication),source:current.source_log_sha256});if(line!==undefined)q.set('line',line);return `/api/runs/${encodeURIComponent(initial.run_id)}/candidates/${encodeURIComponent(current.candidate_id)}/actions?${q}`;}
 async function request(url,c){const response=await fetch(url,{headers:{'X-WorldBloom-Client':'1'}});const value=await response.json();if(!response.ok)throw Error(value.message||'行動ログを読み込めませんでした。');if(value.candidate_id!==c.candidate_id||String(value.publication)!==String(initial.publication)||value.source!==c.source_log_sha256)throw Error('候補と原記録を確認できません。再読み込みしてください。');return value;}
 async function choose(line,focus=false){
  if(!current||!loaded)return;position().line=line;remember();const n=++detailTicket,c=current;
  for(const button of body.querySelectorAll('[data-sa-line]'))button.setAttribute('aria-pressed',String(Number(button.dataset.saLine)===line));
  detail.textContent='行動の詳細を読み込み中…';if(focus)panel.classList.add('sa-show-detail');
  const id=`${c.candidate_id}:${line}`;
  try{let value=details.get(id);if(!value){value=await request(address(line),c);details.set(id,value);if(details.size>40)details.delete(details.keys().next().value);}
   if(n!==detailTicket||current!==c||!active)return;
   const h=document.createElement('h3');h.textContent=value.title;
   const contents=document.createElement('div');contents.className='sa-record';contents.innerHTML=value.html;
   const a=document.createElement('a');a.textContent=`原記録の${line}行目を読む ↗`;a.href=value.raw_href;
   detail.replaceChildren(h,contents,a);if(focus){h.tabIndex=-1;h.focus();}
  }catch(e){if(n!==detailTicket||current!==c||!active)return;detail.replaceChildren();const p=document.createElement('p');p.textContent=e.message;const b=document.createElement('button');b.type='button';b.textContent='詳細を再試行';b.addEventListener('click',()=>choose(line,focus));detail.append(p,b);}
 }
 function draw(){
  const s=state(), pos=position(), rows=loaded.rows.filter(r=>s.actor==='all'||r.actor===s.actor);body.replaceChildren();let previous=null;
  for(const row of rows){
   const day=row.day?`${row.day}日目`:'日：未記録';if(day!==previous){const tr=document.createElement('tr'),th=document.createElement('th');th.colSpan=4;th.className='sa-day';th.textContent=day;tr.append(th);body.append(tr);previous=day;}
   const tr=document.createElement('tr');tr.dataset.saRow=row.line;
   const cells=[`${day} · ${row.slot||'スロット：未記録'}`,row.actor||'人物：未記録',row.action,row.result];
   cells.forEach((text,i)=>{const td=document.createElement('td');if(i===2){const b=document.createElement('button');b.type='button';b.dataset.saLine=row.line;b.textContent=text;b.setAttribute('aria-pressed','false');b.addEventListener('click',()=>choose(row.line,true));td.append(b);}else td.textContent=text;tr.append(td);});body.append(tr);
  }
  $('[data-sa-count]').textContent=`${rows.length}件 / 全${loaded.rows.length}件`;
  say(loaded.invalid_count?`読み取れない記録が${loaded.invalid_count}行あります。原記録で確認できます。`:rows.length?'':'この人物の行動記録はありません。');
  clearDetail();scroll.scrollTop=pos.scroll||0;
  if(rows.length){const line=rows.some(r=>r.line===pos.line)?pos.line:rows[0].line;choose(line);}else ++detailTicket;
 }
 async function load(){
  if(!current||!active)return;const c=current,n=++ticket;++detailTicket;loaded=null;body.replaceChildren();filter.disabled=true;clearDetail();say('行動ログを読み込み中…');$('[data-sa-retry]').hidden=true;
  if(!c.raw_href||!c.source_log_sha256){say(c.availability||'原記録がありません。');$('[data-sa-count]').textContent='';return;}
  try{const value=await request(address(),c);
   if(n!==ticket||current!==c||!active)return;loaded=value;
   filter.replaceChildren();const choices=[['all','全員'],...value.actors.map(actor=>[actor,actor?(actor===value.protagonist?`${actor}（主人公）`:actor):'人物：未記録'])];
   for(const [v,label] of choices){const o=document.createElement('option');o.value=v;o.textContent=label;filter.append(o);}
   const s=state();if(!choices.some(([v])=>v===s.actor))s.actor=value.protagonist&&value.actors.includes(value.protagonist)?value.protagonist:'all';filter.value=s.actor;filter.disabled=false;draw();
  }catch(e){if(n!==ticket||current!==c||!active)return;say(e.message);$('[data-sa-count]').textContent='';$('[data-sa-retry]').hidden=false;}
 }
 root.addEventListener('sf-candidate',e=>{const c=e.detail;if(current?.candidate_id===c.candidate_id)return;remember();current=c;loaded=null;++ticket;++detailTicket;if(active)load();});
 root.addEventListener('sf-panel',e=>{const next=e.detail==='actions';if(next===active)return;active=next;root.classList.toggle('sf-actions-active',active);root.querySelector('.sf-reading').classList.toggle('sf-action-reading',active);if(active)load();else{++ticket;++detailTicket;}});
 filter.addEventListener('change',()=>{remember();state().actor=filter.value;draw();});scroll.addEventListener('scroll',remember,{passive:true});
 $('[data-sa-retry]').addEventListener('click',load);$('[data-sa-back]').addEventListener('click',()=>panel.classList.remove('sa-show-detail'));window.addEventListener('beforeunload',remember);
})();
