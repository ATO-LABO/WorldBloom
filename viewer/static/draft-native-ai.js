((global)=>{'use strict';
const display=(field,value)=>value===null?'制限なし':Array.isArray(value)&&value.every(v=>typeof v==='string')?(value.join('、')||'選択なし'):typeof value==='string'?value:JSON.stringify(value,null,2);
const labels={time:'時間',event:'予定',action:'行動の制限',goal:'人物の目的',commitments:'人物の優先事項'};
const fields={days:'日数',slots:'実行できる時間帯',actors:'実行できる人物',description:'目的の文章',target:'目標の品物',deliver_to:'目標の場所',label:'予定名',day:'実施日',slot:'時間帯',targets:'対象人物',commitments:'優先事項',additions:'追加する条件',grants_item:'品物の付与',grants_fact:'情報の付与',move_to:'移動先',stress_delta:'ストレス変化',force_action:'指定行動'};
global.WorldBloomNativeAI={connect(root,host){
 const panel=root.querySelector('[data-native-ai]');if(!panel)return null;
 const $=name=>panel.querySelector('[data-nai-'+name+']'),uuid=()=>crypto.randomUUID().replaceAll('-','');
 let binding=null,requestId=new URLSearchParams(location.search).get('native_revision'),candidate=null,inFlight=false,cancelling=false;
 if(!/^[a-f0-9]{32}$/.test(requestId||''))requestId=null;
 const mode=panel.dataset.aiMode;
 $('note').textContent=mode==='fixture'?'固定応答で候補と差分を検証します。実AI・GPUは呼びません。':mode==='live'?'選択した設定と登録済みの参照名、要望を現在のAIへ送り、1回問い合わせます。差分を適用するまで下書きは変えません。先に手修正を保存してください。':'修正候補用のAIは未接続です。手入力・保存・再開は利用できます。';
 if(mode==='fixture')$('propose').textContent='修正候補を作る（固定応答）';
 const message=text=>$('status-text').textContent=text;
 function carry(){const u=new URL(location.href);if(requestId)u.searchParams.set('native_revision',requestId);else u.searchParams.delete('native_revision');history.replaceState(history.state,'',u.pathname+u.search+u.hash);}
 function valid(){const s=host.get();return !!candidate&&candidate.state==='ready'&&!s.dirty&&s.record?.revision===candidate.base_revision&&JSON.stringify(candidate.target)===$('target').value&&candidate.instruction===$('instruction').value;}
 function controls(){const s=host.get(),locked=s.busy||s.unknown||inFlight;
  if(inFlight)for(const el of root.querySelectorAll('input,select,textarea,button'))el.disabled=true;
  $('propose').disabled=locked||!s.record?.model||!!requestId||!['live','fixture'].includes(mode);
  $('status').disabled=locked||!requestId;$('cancel').disabled=s.busy||s.unknown||cancelling||!requestId;
  $('target').disabled=locked||!!requestId;$('instruction').disabled=locked||!!requestId;
  $('apply').disabled=locked||!valid();
 }
 function diff(){const box=$('diff');box.replaceChildren();if(!candidate)return;
  for(const change of candidate.changes||[]){const group=document.createElement('section'),title=document.createElement('h3'),before=document.createElement('pre'),after=document.createElement('pre');
   title.textContent=fields[change.field]||change.field;before.textContent='変更前\n'+display(change.field,change.before);after.textContent='候補\n'+display(change.field,change.after);group.append(title,before,after);box.append(group);}
  if(candidate.state==='ready')message('候補を確認してください。まだ下書きには反映していません。');
  else message(({generating:'候補の作成中です。状態の確認はAIを再度呼びません。',stale:'作成後に下書きが更新されました。古い候補は適用できません。',cancelled:'候補を取り消しました。下書きは変更していません。',applied:'この候補は下書きへ適用・保存済みです。再開して保存状態を確認できます。',failed:candidate.message||'候補を作れませんでした。自動再試行しません。'})[candidate.state]||'候補の状態を確認してください。');
 }
 function changed(){if(candidate)message('下書きに未保存の変更があります。候補は自動適用しません。');controls();}
 function refresh(){const s=host.get(),next=s.record?.workspace_id||null;
  if(binding!==next){const u=new URL(location.href);if(binding!==null||u.searchParams.get('native_draft')!==next)requestId=null;candidate=null;binding=next;carry();$('diff').replaceChildren();}
  const old=$('target').value;$('target').replaceChildren();const m=s.record?.model;if(!m){controls();return;}
  const options=[];if(m.ordinary)options.push([{kind:'time',target:null},labels.time]);
  m.scheduled_events.forEach((e,i)=>options.push([{kind:'event',target:i},'予定：'+(e.label||e.id)]));
  for(const ref of m.actions)if(ref!=='verb:rest')options.push([{kind:'action',target:ref},'行動：'+(m.action_labels?.[ref]||ref)]);
  for(const p of m.people)for(const kind of ['goal','commitments'])options.push([{kind,target:p.name},p.name+'：'+labels[kind]]);
  for(const [target,label]of options){const o=document.createElement('option');o.value=JSON.stringify(target);o.textContent=label;$('target').append(o);}
  if(options.some(([t])=>JSON.stringify(t)===old))$('target').value=old;
  if(candidate){$('target').value=JSON.stringify(candidate.target);diff();}controls();
 }
 async function run(fn){const s=host.get();if(inFlight||s.busy||s.unknown)return;inFlight=true;controls();try{await fn();}catch(e){message((e.message||'通信に失敗しました。')+' 自動再送はしません。「候補の状態を確認」で読み直せます。');}finally{inFlight=false;host.controls();}}
 $('propose').addEventListener('click',()=>{const s=host.get();if(s.dirty){message('先に手修正を下書きへ保存してください。AIは呼んでいません。');return;}if(requestId||!$('instruction').value.trim())return;
  requestId=uuid();const submitted=requestId;candidate=null;carry();run(async()=>{message('候補を作成しています。下書きは変更しません。');const result=await host.api('/'+s.record.workspace_id+'/revise',{revision:s.record.revision,request_id:submitted,target:JSON.parse($('target').value),instruction:$('instruction').value});if(requestId===submitted){candidate=result;diff();}});
 });
 $('status').addEventListener('click',()=>run(async()=>{const s=host.get();candidate=await host.api('/'+s.record.workspace_id+'/revisions/'+requestId);if(candidate.target)$('target').value=JSON.stringify(candidate.target);if(candidate.instruction)$('instruction').value=candidate.instruction;diff();}));
 $('cancel').addEventListener('click',async()=>{const s=host.get();if(cancelling||s.busy||s.unknown||!requestId)return;cancelling=true;controls();try{await host.api('/'+s.record.workspace_id+'/cancel-revision',{request_id:requestId});candidate=null;requestId=null;carry();$('diff').replaceChildren();message('候補を取り消しました。下書きは変更していません。'+(inFlight?' AIへの問い合わせ自体は完了待ちです。戻ってきた候補は適用しません。':''));}catch(e){message(e.message+' 候補の状態を確認してください。');}finally{cancelling=false;host.controls();}});
 $('apply').addEventListener('click',()=>{if(!valid())return;const s=host.get(),id=s.record.workspace_id,revision=s.record.revision,captured=JSON.stringify(s.buffer);run(async()=>{
  const r=await host.api('/'+id+'/apply-revision',{revision,request_id:requestId});const current=host.get();
  if(current.record?.workspace_id!==id||current.record?.revision!==revision||JSON.stringify(current.buffer)!==captured){message('現在の入力が変わったため置き換えません。保存済みの下書きを確認してください。');return;}
  requestId=null;candidate=null;carry();host.load(r);$('diff').replaceChildren();message('候補を下書きへ適用・保存しました。元世界は変更していません。世界採用には別の確認が必要です。');
 });});
 controls();return {controls,refresh,changed};
}};
})(window);
