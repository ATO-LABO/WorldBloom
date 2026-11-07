/* Explicit first-input generation. Durable request receipts; no automatic POST replay. */
((global)=>{'use strict';
const M=global.WorldBloomCreationModel,copy=M.copy,fp=M.fingerprint;
const prefix='worldbloom.creation-generation.v1.';
const settled=new Set(['failed','discarded']);
function receiptStorage(target){return {getItem(key){try{return target.localStorage.getItem(key);}catch(_){throw Error('このブラウザーでは送信記録を読み取れません。AIは再送しません。');}},setItem(key,value){try{target.localStorage.setItem(key,value);}catch(_){throw Error('このブラウザーでは送信前の入力とIDを保持できません。AIは開始していません。マニュアルで続けられます。');}}};}
function eligible(content){
 let d;try{d=content.draft_text?JSON.parse(content.draft_text):M.empty(content.name,content.overview);}catch(_){return false;}
 if(!d||typeof d!=='object'||Array.isArray(d))return false;
 const scalar=new Set(['name','overview','settings']),emptyText=new Set(['initial_story','protagonist','antagonist']),emptyLists=new Set(['people','places','items','routes','facts']);
 // Unknown draft fields, even false/0/empty values, are human data. Do not merge
 // them by truthiness or allow the whole-world generator to replace them.
 for(const [key,value]of Object.entries(d)){
  if(scalar.has(key))continue;
  if(emptyText.has(key)&&value==='')continue;
  if(emptyLists.has(key)&&Array.isArray(value)&&value.length===0)continue;
  return false;
 }
 return !!(d.settings===undefined||d.settings&&typeof d.settings==='object'&&!Array.isArray(d.settings));
}
function payload(c,id,strategy=null){
 if(strategy!==null&&strategy!=='goal-plan-v1')throw Error('未対応の生成方式です。');
 const d=c.draft_text?JSON.parse(c.draft_text):null;
 return {...(strategy?{strategy}:{}),request_id:id,name:c.name,overview:c.overview,...(d?.settings!==undefined?{settings:copy(d.settings)}:{}),authoring:copy(c.authoring)};
}
function ready(result){return result.state==='review'||result.state==='invalid'&&result.editable_generated_candidate===true;}
function metadata(result){return result.strategy==='goal-plan-v1'?{strategy:result.strategy,...(result.goal_plan?{goal_plan:copy(result.goal_plan)}:{}),...(result.goal_plan_notes?{goal_plan_notes:copy(result.goal_plan_notes)}:{}),model_calls:result.model_calls||0}:null;}
function phaseText(result){return result.strategy==='goal-plan-v1'?'3段階：'+({extract:'1/3 目的を解釈',generate:'2/3 世界設定を生成',review:'3/3 内容を確認',complete:'処理終了'}[result.phase]||'処理の開始を確認中')+' ／ AI呼出し '+(result.model_calls||0)+'/3回':'生成と内容確認を行っています。';}
function entered(c,result){
 if(!eligible(c)||!ready(result)||!result.draft||!Array.isArray(result.draft.people)||!Array.isArray(result.draft.places))throw Error('生成結果を入力できません。元の入力は保持しています。');
 const d=copy(result.draft),original=c.draft_text?JSON.parse(c.draft_text):null;
 d.name=c.name;d.overview=c.overview;
 if(original&&Object.hasOwn(original,'settings'))d.settings=copy(original.settings);
 else delete d.settings;
 return {...copy(c),...(metadata(result)?{generation:metadata(result)}:{}),draft_text:JSON.stringify(d,null,2),time:M.workspaceTime(d,c.time),source_draft_id:result.request_id,source_revision:result.edit_revision||result.revision,workflow:{...copy(c.workflow),step:2,category:'people',selected:0}};
}
class Session{
 constructor({storage,api,uuid}){this.storage=storage;this.api=api;this.uuid=uuid;this.receipt=null;this.inFlight=false;}
 persist(r){const raw=JSON.stringify(r),key=prefix+r.id;this.storage.setItem(key,raw);if(this.storage.getItem(key)!==raw)throw Error('送信前の入力と確認IDを保持できません。AIは開始していません。');this.receipt=r;return r;}
 read(id){if(!/^[a-f0-9]{32}$/.test(id||''))return null;const raw=this.storage.getItem(prefix+id);if(!raw)throw Error('このブラウザーに送信時の入力記録がありません。AIを再送せず、元の作成タブから確認してください。');const r=JSON.parse(raw);if(r.version!==1||r.id!==id||fp(r.content)!==r.signature)throw Error('送信時の入力記録を確認できません。AIは再送しません。');this.receipt=r;return copy(r);}
 async send(snapshot,onPrepared,strategy=null){
  if(this.inFlight||this.receipt&&!settled.has(this.receipt.state))throw Error('前回のAI入力の状態を確認してください。再送していません。');
  if(!eligible(snapshot.content))throw Error('入力済みの設定はAIで置き換えません。マニュアルで続けてください。');
  this.inFlight=true;
  try{
   const r=this.persist({version:1,id:this.uuid(),workspace_id:snapshot.id,revision:snapshot.revision,content:copy(snapshot.content),signature:fp(snapshot.content),strategy,state:'pending',detached:false});
   onPrepared(r);
   const result=await this.api('/api/world-drafts',payload(r.content,r.id,r.strategy));
   const record={...this.receipt};delete record.result;
   this.persist({...record,result_state:result.state,state:ready(result)?'ready':result.state==='generating'?'pending':result.state==='unknown'?'unknown':'failed'});
   return result;
  }catch(e){if(this.receipt&&this.receipt.state==='pending')this.persist({...this.receipt,state:e.known?'failed':'unknown',error:e.message});throw e;}
  finally{this.inFlight=false;}
 }
 async check(){
  if(!this.receipt)throw Error('確認できる送信IDがありません。');
  const result=await this.api('/api/world-drafts/'+this.receipt.id);
  const record={...this.receipt};delete record.result;
  this.persist({...record,result_state:result.state,state:ready(result)?'ready':result.state==='generating'?'pending':result.state==='unknown'?'unknown':'failed'});return result;
 }
 detach(){if(this.receipt)this.persist({...this.receipt,detached:true});}
 matches(s){const r=this.receipt;return !!r&&s.kind==='new'&&s.id===r.workspace_id&&s.revision===r.revision&&fp(s.content)===r.signature;}
 async current(s){if(!this.matches(s))return false;const latest=await this.api('/api/world-draft-workspaces/'+this.receipt.workspace_id);return latest.revision===this.receipt.revision&&fp(latest.content)===this.receipt.signature;}
 mark(state){if(this.receipt)this.persist({...this.receipt,state});}
}
function mount(root,adapter){
 const $=key=>root.querySelector('[data-cwg-'+key+']'),session=new Session({storage:receiptStorage(global),api:adapter.api,uuid:adapter.uuid});
 const dialog=$('dialog'),title=$('title'),body=$('body'),actions=$('actions');let working=false,opener=null,mode='confirm',sequence=0,visibleRun=0,progressLine=null;
 const el=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
 function action(label,fn,style=''){const b=el('button',label,'ui-button'+(style?' ui-button--'+style:''));b.type='button';b.addEventListener('click',fn);actions.append(b);if(actions.children.length===1)b.focus();return b;}
 function tracking(){const r=session.receipt;$('recovery').hidden=!r||settled.has(r.state);$('status').textContent=r?'送信したAI入力：'+({pending:'処理中または結果待ち',unknown:'結果不明',ready:'未反映の結果あり',failed:'設定入力は未完了',applied:'下書きへ入力済み'}[r.state]||r.state):'';$('recover').dataset.cwLocked=String(working);$('recover').disabled=working;}
 function close({preserveRun=false}={}){if(!preserveRun){++sequence;visibleRun=0;}if(dialog.open)dialog.close();adapter.lock(false);opener?.focus?.();}
 const presenting=run=>!!run&&run===visibleRun&&dialog.open;
 function show(kind,heading,text){mode=kind;title.textContent=heading;progressLine=el('p',text);body.replaceChildren(progressLine);actions.replaceChildren();if(!dialog.open)dialog.showModal();}
 function updateProgress(result){if(mode==='progress'&&dialog.open&&progressLine){progressLine.textContent=phaseText(result);return;}show('progress','AIで設定を作成中',phaseText(result));progressActions();}
 function leave(step){++sequence;visibleRun=0;try{session.detach();}catch(_){adapter.message('送信記録を更新できません。元の送信IDは保持し、再送しません。');}close();if(step===2)adapter.manual();tracking();}
 function manual(){if(working)leave(2);else{close();adapter.manual();}}
 function error(text){show('error','AIでの設定入力を完了できませんでした',text+' 元の入力は保持しています。自動再送はしません。');action('戻る',close);action('マニュアルで入力する',manual);if(session.receipt&&!settled.has(session.receipt.state))action('送信した結果を確認',recover);if(session.receipt?.state==='ready')action('この結果を使わず手入力を続ける',()=>{session.mark('discarded');adapter.track(null);close();adapter.manual();tracking();});}
 function unavailable(status){
  const missing=status.status==='unconfigured',invalid=status.status==='invalid_settings';
  show('unavailable',missing?'AIの設定が必要です':invalid?'AI設定を読み取れません':'AI入力を開始できません',missing?'接続先とモデルを設定してください。入力した概要やJSONは保持しています。':invalid?'設定の形式を確認してください。未設定や接続障害とは区別し、AIは開始していません。':status.status==='insufficient_budget'?'3段階処理にはAI呼出し上限3回以上が必要です。設定を確認してください。マニュアル入力で続けられます。':status.status==='busy'?'この接続では別の生成処理が進行中です。AIは開始していません。':'この起動版では、設定を保持して生成できる接続を確認できません。マニュアルで続けられます。');
  action('戻る',close);action('マニュアルで入力する',manual);
  if(status.settings_route==='/configs?tab=output'){
   body.append(el('p','AI設定を開く前に、現在の下書きを①の位置で保存します。戻っても自動生成しません。','ui-hint'));
   action('AI設定を開く',async()=>{if(working)return;working=true;close();adapter.lock(true);try{if(!await adapter.save())return;const s=adapter.snapshot();global.location.href='/configs?tab=output&creation_workspace='+s.id;}finally{working=false;adapter.lock(false);tracking();}} ,'advance');
  }
 }
 async function applyResult(result,{automatic=false,run=0}={}){
  if(!presenting(run)||automatic&&session.receipt.detached)return false;
  if(!ready(result)){
   if(result.state==='generating'){updateProgress(result);return false;}
   const reason={gpu_busy:'GPUが別の処理で使用中です。',launch_failed:'AI接続または起動に失敗しました。',transport_timeout:'AIとの通信がタイムアウトしました。',transport_disconnected:'AIとの接続が切れました。',review_failed:'生成後の内容確認を完了できませんでした。',review_unknown:'生成後の内容確認の結果が不明です。'}[result.failure_reason]||'';
   const detail=reason+Object.values(result.errors||{}).join(' / ');
   error((result.state==='invalid'?'生成された設定、または内容確認に問題があり、下書きへ入力していません。':result.state==='cancelled'?'送信記録は取消済みです。実際のAI処理が止まったことは保証しません。':'生成または接続の結果を確定できません。')+(detail?' '+detail:''));return false;
  }
  const snapshot=adapter.snapshot();
  const current=session.matches(snapshot)&&await session.current(snapshot);
  // The proposal GET and the saved-workspace GET are both async boundaries.
  // Closing, Escape or manual navigation invalidates every continuation,
  // including error/progress presentation in an explicitly requested recovery.
  if(!presenting(run)||automatic&&session.receipt.detached)return false;
  if(!current||!session.matches(adapter.snapshot())){
   error('送信後に入力・保存版・表示する下書きが変わりました。古い結果を現在の入力へ上書きしません。');return false;
  }
  const next=entered(session.receipt.content,result);session.mark('applied');visibleRun=0;close();adapter.apply(next,result);tracking();return true;
 }
 function poll(run,id){
  if(typeof global.setTimeout!=='function')return;
  global.setTimeout(async()=>{
   if(!session.inFlight||!presenting(run)||session.receipt?.detached)return;
   try{const result=await adapter.api('/api/world-drafts/'+id);if(!session.inFlight||!presenting(run)||session.receipt?.detached)return;
    if(result.state==='generating'){updateProgress(result);poll(run,id);}
   }catch(_){if(presenting(run))body.append(el('p','進捗を取得できません。自動再試行を止めました。送信結果を待つか、後で状態を確認してください。','ui-hint'));}
  },1500);
 }
 function progressActions(){
  body.prepend(el('span','', 'cw-generation-spinner'));body.append(el('p','入力は保持しています。画面を戻っても、送信済みのAI処理は続く場合があります。自動再送はしません。','ui-hint'));
  action('①へ戻る（入力を保持）',()=>leave(1));action('マニュアルで入力する',()=>leave(2));
 }
 async function start(strategy=null){
  if(working)return;working=true;const run=++sequence;visibleRun=run;adapter.lock(true);actions.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{
   if(session.receipt&&!settled.has(session.receipt.state)){show('recover','前回のAI入力を確認してください','送信済みのIDを保持しています。新しい生成は開始せず、結果を読取確認できます。');action('戻る',close);action('送信した結果を確認',recover);return;}
   const initial=adapter.snapshot();
   if(!initial.content.name.trim()||!initial.content.overview.trim()){show('confirm','名前と概要を入力してください','AI入力には世界の名前と概要が必要です。手入力なら空欄のまま進めます。');action('戻る',close);action('マニュアルで入力する',manual);return;}
   const status=await adapter.api('/api/world-draft-generation');if(run!==visibleRun)return;if(status.status!=='configured'){unavailable(status);return;}
   if(strategy&&status.strategies?.[strategy]?.status!=='configured'){unavailable({...status.strategies?.[strategy],settings_route:'/configs?tab=output'});return;}
   if(!eligible(initial.content)){show('guard','入力済みの設定を保持します','人物・場所・関係・導入文や未対応項目などの入力済み設定があります。このフローでは置き換えず、AIも開始しません。');action('戻る',close);action('マニュアルで入力する',manual);return;}
   close({preserveRun:true});adapter.lock(true);
   if(!await adapter.save()){adapter.message('処理前の下書きを保存できないため、AIは開始していません。入力を保持しています。');return;}
   if(run!==visibleRun)return;
   show('progress','AIで設定を作成中','設定を生成し、構造・参照と内容を確認しています。完了後に②へ進みます。');progressActions();
   const result=await session.send(adapter.snapshot(),r=>{adapter.track(r.id);tracking();if(strategy)poll(run,r.id);},strategy);
   if(run===visibleRun)await applyResult(result,{automatic:true,run});
  }catch(e){if(run===visibleRun)error(e.message==='Failed to fetch'?(session.receipt?'通信が切れ、処理結果を確認できません。':'AI設定を確認する通信に失敗しました。AIは開始していません。'):e.message);}finally{working=false;adapter.lock(false);tracking();}
 }
 async function recover(){if(working)return;working=true;const run=++sequence;visibleRun=run;adapter.lock(true);tracking();try{const r=await session.check();if(!presenting(run))return;await applyResult(r,{run});}catch(e){if(presenting(run))error(e.status===404?'送信済み記録がまだ見つかりません。未送信とは断定せず、同じIDを保持します。':e.message);}finally{working=false;adapter.lock(false);tracking();}}
 function open(){
  if(working){tracking();return;}opener=document.activeElement;
  if(session.receipt&&!settled.has(session.receipt.state)){show('recover','前回のAI入力を確認してください','処理結果が未確定です。再送せず、保存された結果を確認できます。');action('戻る',close);action('マニュアルで入力する',manual);action('送信した結果を確認',recover);return;}
  show('confirm','概要から設定を入力しますか？','概要をもとに、人物・場所・持ち物・関係などをAIが推定して入力します。入力した内容は、次の画面で確認・修正できます。');
  body.append(el('p','概要にない設定を補うことがあります。処理前の下書きを保存します。世界への採用は、最後の確認後に行います。生成後の内容確認でもAIを使う場合があります。','ui-notice'));
  action('キャンセル',close);action('マニュアルで入力する',manual);
  const plan=action('3段階で設定を作る',()=>start('goal-plan-v1'),'advance');plan.disabled=true;
  const note=el('p','3段階方式の設定を確認中です。','ui-hint');body.append(note);
  const opening=sequence;adapter.api('/api/world-draft-generation').then(status=>{if(!dialog.open||mode!=='confirm'||sequence!==opening)return;const p=status.strategies?.['goal-plan-v1'];plan.disabled=p?.status!=='configured';note.textContent=p?.status==='configured'?'目的の解釈 → 世界設定の生成 → 内容確認。最大3回のAI呼出しで、解釈と補完は後から修正できます。':p?.status==='insufficient_budget'?'3段階方式には呼出し上限3回以上が必要です。設定を確認するか、マニュアル入力で続けてください。':'この接続では3段階方式を利用できません。マニュアル入力で続けられます。';}).catch(()=>{if(dialog.open&&mode==='confirm'&&sequence===opening)note.textContent='3段階方式の設定を確認できません。AIは開始していません。';});
 }
 dialog.addEventListener('cancel',e=>{e.preventDefault();if(working)leave(1);else close();});
 dialog.addEventListener('keydown',e=>{if(e.key!=='Tab')return;const buttons=[...actions.querySelectorAll('button')].filter(b=>!b.disabled);if(!buttons.length){e.preventDefault();return;}const first=buttons[0],last=buttons[buttons.length-1];if(e.shiftKey&&(document.activeElement===first||document.activeElement===dialog)){e.preventDefault();last.focus();}else if(!e.shiftKey&&(document.activeElement===last||document.activeElement===dialog)){e.preventDefault();first.focus();}});
 $('recover').addEventListener('click',()=>{opener=document.activeElement;show('recover','送信したAI入力を確認','AIは再送せず、保存された生成結果を読み取ります。');action('戻る',close);action('生成結果を確認して入力',recover);});
 function resume(id){try{if(id)session.read(id);tracking();}catch(e){adapter.message(e.message);}}
 return {open,resume,session,active:()=>working||!!session.receipt&&!settled.has(session.receipt.state),saved(){const r=session.receipt;if(r?.state==='applied'&&adapter.snapshot().id===r.workspace_id){try{session.mark('discarded');}catch(_){session.receipt={...r,state:'discarded'};}adapter.track(null);tracking();}}};
}
global.WorldBloomCreationGeneration={eligible,payload,entered,ready,metadata,phaseText,receiptStorage,Session,mount};
})(window);
