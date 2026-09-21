"use strict";
(() => {
 const root=document.querySelector('[data-generation]');if(!root)return;
 const $=s=>root.querySelector(`[data-gen-${s}]`), initial=JSON.parse(root.dataset.initial||'{}');
 const modal=root.tagName==='DIALOG', terminal=new Set(['succeeded','partial','failed','cancelled','interrupted']);
 const statuses={pending:'未開始',running:'生成中',ok:'生成済み',prompt_only:'プロンプト保存済み',unknown:'結果不明',error:'失敗',skipped_limit:'上限により未実行',skipped_cancelled:'停止により未実行',skipped_interrupted:'中断により未実行',cancelled:'停止',interrupted:'中断'};
 const completions={generated:'全件生成',prompt_only:'プロンプト保存のみ（文章は未生成）',mixed:'文章とプロンプトの混在',partial:'一部成功',partial_unknown:'一部成功（結果不明あり）',unknown:'結果不明',limit_before_start:'呼出し前に上限到達',error:'失敗',cancelled:'停止済み',interrupted:'中断'};
 const states={queued:'受付済み',running:'生成中',starting:'準備中',stopping:'停止処理中',succeeded:'完了',partial:'一部完了',failed:'失敗',cancelled:'停止済み',interrupted:'中断'};
 let plan=null,job=null,output=null,request=null,source=null,opener=null,busy=false,uncertain=false,stopConfirm=false,pollTimer=null,version=0,connectionLost=false;
 const applied=new Set(), texts=new Map();
 const reopen=document.createElement('button');reopen.type='button';reopen.className='gen-reopen';reopen.hidden=true;document.body.append(reopen);
 function show(from){opener=from||document.activeElement;if(modal&&!root.open)root.showModal();}
 function restoreFocus(){if(opener?.isConnected&&opener.getClientRects().length){opener.focus();return;}const fallback=document.querySelector('[data-sf-title]')||document.querySelector('[data-sf-proceed]');if(fallback){fallback.setAttribute('tabindex','-1');fallback.focus();}}
 function close(){if(modal){root.close();restoreFocus();}else{const rid=job?.run_id||plan?.run_id||request?.run_id;if(rid)location.href=`/runs/${encodeURIComponent(rid)}/candidates`;}}

 function error(text){$('error').textContent=text||'';}
 function key(rid){return `wb-generation:${rid}`;}
 function persist(){const rid=request?.run_id||job?.run_id;if(!rid)return;try{sessionStorage.setItem(key(rid),JSON.stringify({request,job_id:job?.job_id,source,uncertain}));}catch{}}
 function saved(rid){try{return JSON.parse(sessionStorage.getItem(key(rid))||'null');}catch{return null;}}
 async function api(url,body){const response=await fetch(url,{method:body?'POST':'GET',headers:{'X-WorldBloom-Client':'1',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});let value;try{value=await response.json();}catch{throw Error('応答を確認できません');}if(!response.ok){const e=Error(value.message||'処理できませんでした');e.definite=response.status>=400&&response.status<500;e.status=response.status;throw e;}return value;}
 function syncActivity(){
  const accepting=busy&&!job;
  const running=!!job&&['queued','starting','running','stopping'].includes(job.state)&&job.reconciliation!=='unknown'&&!connectionLost;
  $('primary').classList.toggle('gen-working',accepting);
  reopen.classList.toggle('gen-working',accepting||running);
 }
 function button(text,disabled=false){$('primary').textContent=text;$('primary').disabled=disabled;$('primary').hidden=false;syncActivity();}
 function reset(){clearTimeout(pollTimer);version++;job=null;output=null;plan=null;request=null;uncertain=false;stopConfirm=false;connectionLost=false;syncActivity();texts.clear();applied.clear();$('settings').replaceChildren();$('entries').replaceChildren();$('ack').checked=false;error('');}
 function base(){if(uncertain)$('note').textContent='処理が始まっている可能性があります。同じ要求で受付結果を確認します。';for(const el of $('settings').querySelectorAll('select,button'))el.disabled=busy||uncertain||!!job;const kind=job?.kind||plan?.kind||request?.kind;$('title').textContent=kind==='narrate'?'本文を生成':'あらすじを準備';$('close').hidden=!modal;$('ack-wrap').hidden=!request?.acknowledge_unknown||!!job;$('progress').hidden=!job;$('stop').hidden=!job||terminal.has(job.state);$('stop').disabled=busy||!!job?.cancel_requested_at;$('details').hidden=!job;$('result').hidden=true;$('secondary').textContent=job||uncertain?'閉じる':'戻る';reopen.hidden=!modal||!job&&!uncertain;reopen.textContent=uncertain?'生成の受付状況を確認':`${job?.kind==='narrate'?'本文':'あらすじ'}の生成 · ${states[job?.state]||job?.state||''}`;syncActivity();}
 function renderPlan(){base();$('message').textContent='対象と設定を確認してから開始します。';$('note').textContent='生成したあとも、この画面で候補選びを続けられます。';$('entries').replaceChildren();for(const c of plan.candidates||[]){const li=document.createElement('li'),name=document.createElement('strong'),state=document.createElement('span');name.textContent=c.label;state.textContent=c.eligible?'今回の対象':'対象外';li.append(name,state);$('entries').append(li);}
  const settings=document.createElement('div');settings.className='gen-settings';const info=document.createElement('p');const n=request?.candidate_ids.length||0,l=plan.limits?.max_calls;info.textContent=`対象 ${n}件${plan.backend==='none'?' · プロンプト保存のみ':`${l!=null?' ／ 呼出し上限 '+l+'件':''} · ${plan.backend||'未設定'}${plan.model?' / '+plan.model:''}`}`;settings.append(info);
  if(plan.legacy){const label=document.createElement('label');label.textContent='旧実験の文章化用設定';const select=document.createElement('select');select.setAttribute('aria-label','旧実験の文章化用設定');const empty=new Option('設定を選択','');select.append(empty);for(const c of plan.configs||[])select.add(new Option(c.label,c.config_id));select.value=plan.config_id||'';label.append(select);const confirm=document.createElement('button');confirm.type='button';confirm.textContent='この設定で確認';confirm.onclick=()=>{if(!select.value)return;const u=new URL(source,location.origin);u.searchParams.set('config',select.value);loadPlan(u.href);};settings.append(label,confirm);}
  $('settings').replaceChildren(settings);error((plan.errors||[]).join('\n'));
  button(plan.backend==='none'?`${n}件のプロンプトを保存`:l<n?`上限${l}件で生成を開始`:`${n}件の生成を開始`,!request);
  if(plan.backend==='none')$('note').textContent='プロンプトのみ保存します。文章は生成されません。';
  if(plan.active_job){$('message').textContent='この実行には処理中のジョブがあります。';button('進行中の処理を確認');}
 }
 async function loadPlan(url){if(busy)return;reset();source=url;busy=true;button('対象を確認中…',true);$('message').textContent='生成対象を確認しています。';const token=version;
  try{const u=new URL(url,location.origin);u.pathname=u.pathname.replace('/runs/','/api/runs/').replace('/generate','/generation-plan');const reviewed=await api(u.pathname+u.search);if(token!==version)return;plan=reviewed;request=plan.request;renderPlan();}catch(e){error(e.message);button('内容を再確認');}finally{busy=false;syncActivity();}
 }
 async function watch(jid){const token=version;let readFailed=false;clearTimeout(pollTimer);try{const latest=await api(`/api/jobs/${encodeURIComponent(jid)}`);if(token!==version)return;job=latest;uncertain=false;persist();const projected=job.output_id?await api(`/api/outputs/${encodeURIComponent(job.output_id)}`):null;if(token!==version)return;output=projected;
   for(const entry of output?.entries||[]){if(entry.status!=='ok'||!entry.text_sha256)continue;const identity=`${output.output_id}:${entry.candidate_id}:${entry.text_sha256}`;if(texts.has(identity))continue;
    const response=await fetch(`/outputs/${encodeURIComponent(output.output_id)}/entries/${encodeURIComponent(entry.candidate_id)}/text`);if(!response.ok)throw Error('生成された文章の整合性を確認できません。詳細の記録を確認してください。');const text=await response.text();if(token!==version)return;texts.set(identity,text);
    if(job.kind==='synopsize'&&!applied.has(identity)){applied.add(identity);window.dispatchEvent(new CustomEvent('wb-generation-entry',{detail:{run_id:job.run_id,candidate_id:entry.candidate_id,text,output_id:output.output_id,text_sha256:entry.text_sha256}}));}
   }
   connectionLost=false;error('');renderJob();
  }catch(e){if(token!==version)return;readFailed=true;connectionLost=true;error(e.message+'。自動生成は行わず、状態の確認だけを続けます。');if(job)renderJob();else button('進捗を再確認');}
  if(token===version&&(readFailed||!job||!terminal.has(job.state)||!output&&job.output_id))pollTimer=setTimeout(()=>watch(jid),1800);
 }
 function renderJob(){base();$('settings').replaceChildren();$('message').textContent=stopConfirm?'生成を停止しますか？ 保存済みの文章は残ります。':job.reconciliation==='unknown'?'実行状態を確認中':terminal.has(job.state)?completions[output?.completion_kind||job.completion_kind]||states[job.state]:states[job.state]||job.state;
  const p=job.progress||{},counts=job.counts||p.counts||{},total=p.total,completed=p.completed;
  $('count').textContent=Number.isFinite(total)?`処理済み ${completed||0} / ${total}件`:'準備中';const meter=$('progress').querySelector('progress');if(Number.isFinite(total)&&total>0){meter.max=total;meter.value=completed||0;}else meter.removeAttribute('value');
  $('phase').textContent=Object.entries(counts).map(([k,n])=>`${statuses[k]||k} ${n}件`).join(' · ')||'保存された進捗を確認しています';
  $('note').textContent=terminal.has(job.state)?'生成結果を確認して、次の操作へ進めます。':'閉じても生成は続きます。停止する場合は「生成を停止」を選んでください。';
  $('id').textContent=`処理 ${job.job_id}`;$('record').hidden=!job.output_id;if(job.output_id)$('record').href=`/outputs/${encodeURIComponent(job.output_id)}?view=record`;
  const scroll=$('entries').parentElement.scrollTop;$('entries').replaceChildren();
  const entries=output?.entries||request?.candidate_ids.map(candidate_id=>({candidate_id,status:'pending'}))||[];
  for(const entry of entries){const li=document.createElement('li'),name=document.createElement('strong'),state=document.createElement('span');name.textContent=plan?.candidates?.find(c=>c.candidate_id===entry.candidate_id)?.label||entry.candidate_id.slice(0,18)+'…';state.textContent=statuses[entry.status]||entry.status;li.append(name,state);const identity=`${output?.output_id}:${entry.candidate_id}:${entry.text_sha256}`;if(texts.has(identity)){const p=document.createElement('p');const text=texts.get(identity);p.textContent=job.kind==='narrate'&&text.length>320?text.slice(0,320)+'…（続きは上映で読めます）':text;li.append(p);}$('entries').append(li);}
  $('entries').parentElement.scrollTop=scroll;
  $('primary').hidden=true;
  if(stopConfirm){button('停止する',busy);$('secondary').textContent='生成を続ける';}
  else if(terminal.has(job.state)){const anyOk=output?.entries?.some(e=>e.status==='ok');if(job.kind==='narrate'&&anyOk){$('result').href=`/outputs/${encodeURIComponent(job.output_id)}`;$('result').textContent='上映で本文を読む →';$('result').hidden=false;}else{button(job.kind==='synopsize'?'候補選びに戻る':'結果を確認');}if(job.error)error(job.error.message||'生成を完了できませんでした。詳細の記録を確認してください。');}
 }
 async function submit(){if(busy||!request)return;if(request.acknowledge_unknown&&!$('ack').checked){error('結果不明の再生成について確認してください。');return;}busy=true;uncertain=true;persist();base();button('受付を確認中…',true);$('message').textContent='生成を受け付けています。';error('');
  try{job=await api('/api/jobs',request);if(!job.job_id)throw Error('受付結果を確認できません');uncertain=false;persist();await watch(job.job_id);}
  catch(e){if(e.definite){try{sessionStorage.removeItem(key(request.run_id));}catch{}uncertain=false;request=null;button('内容を再確認');}else{button('同じ要求で受付を再確認');}base();$('message').textContent=uncertain?'受付結果を確認できません':'開始できませんでした';error(e.message+(uncertain?'。同じ要求IDで再確認できます。自動で再送しません。':''));}
  finally{busy=false;syncActivity();if(job)renderJob();}
 }
 async function open(url,from){show(from);if(busy||uncertain||job&&!terminal.has(job.state)){base();return;}const u=new URL(url,location.origin),rid=u.pathname.split('/')[2],stored=saved(rid);if(stored?.uncertain&&stored.request){reset();source=stored.source;request=stored.request;uncertain=true;base();button('同じ要求で受付を再確認');$('message').textContent='前回の受付結果が未確認です。';return;}await loadPlan(url);}
 async function start(reviewed,from){show(from);if(busy||uncertain||job&&!terminal.has(job.state)){base();return;}reset();request=reviewed;plan={kind:reviewed.kind,run_id:reviewed.run_id,candidates:[]};$('ack').checked=reviewed.acknowledge_unknown;await submit();}
 $('primary').onclick=async()=>{if(busy)return;if(stopConfirm){busy=true;try{await api(`/api/jobs/${encodeURIComponent(job.job_id)}/cancel`,{});stopConfirm=false;await watch(job.job_id);}catch(e){error(e.message);}finally{busy=false;renderJob();}return;}if(job){if(terminal.has(job.state)){if(modal)close();else location.href=job.kind==='synopsize'?`/runs/${encodeURIComponent(job.run_id)}/candidates`:job.output_id?`/outputs/${encodeURIComponent(job.output_id)}?view=record`:`/outputs?run=${encodeURIComponent(job.run_id)}`;}else await watch(job.job_id);return;}if(plan?.active_job){const active=plan.active_job;if(['synopsize','narrate'].includes(active.kind))await watch(active.job_id);else location.href=`/jobs/${encodeURIComponent(active.job_id)}`;return;}if(request)await submit();else if(source)await loadPlan(source);};
 $('secondary').onclick=()=>{if(stopConfirm){stopConfirm=false;renderJob();}else close();};$('close').onclick=close;$('stop').onclick=()=>{stopConfirm=true;renderJob();};reopen.onclick=()=>show(reopen);
 if(modal){root.addEventListener('keydown',e=>{if(e.key!=='Tab')return;const controls=[...root.querySelectorAll('button,a[href],input,select,textarea,summary,[tabindex]')].filter(el=>!el.disabled&&el.tabIndex>=0&&el.getClientRects().length);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}});root.addEventListener('close',restoreFocus);root.addEventListener('cancel',()=>{stopConfirm=false;if(job)renderJob();});}
 window.WBGeneration={open,start};
 if(initial.job){job=initial.job;renderJob();watch(job.job_id);}else if(initial.plan_url)open(initial.plan_url);
 else {const sf=document.querySelector('[data-sifting]');const data=JSON.parse(sf?.dataset.initial||'{}');if(data.run_id){const stored=saved(data.run_id);if(stored?.uncertain&&stored.request){request=stored.request;source=stored.source;uncertain=true;base();button('同じ要求で受付を再確認');$('message').textContent='前回の受付結果が未確認です。';}else api('/api/jobs').then(value=>{const active=value.jobs.find(j=>j.run_id===data.run_id&&!terminal.has(j.state)&&['synopsize','narrate'].includes(j.kind));if(active&&!busy&&!request&&!plan&&!job)watch(active.job_id);}).catch(()=>{});}}
})();
