/* One authoring buffer across three stages. Navigation never calls a model. */
(()=>{'use strict';
const root=document.querySelector('[data-creation-workflow]');if(!root)return;
const M=window.WorldBloomCreationModel,$=k=>root.querySelector('[data-cw-'+k+']'),copy=M.copy,fp=M.fingerprint,uuid=()=>crypto.randomUUID().replaceAll('-','');
const categories=[['people','人物'],['places','場所'],['items','持ち物'],['time','時間・予定'],['actions','行動・ルール']];
const S={screen:'entry',active:false,step:1,category:'people',selected:0,kind:'new',draft:null,raw:'',originalContent:{},authoring:null,prep:{name:'',overview:'',genre:'',storyType:''},id:null,revision:null,saved:null,proposal:null,native:null,buffer:null,checked:null,busy:false,unknown:false,conflict:false,pending:null,ai:null,aiId:null,aiBase:null,aiCancelled:false,epoch:0,published:null};
let historyIndex=0,bouncing=false,formsEditor=null,aiTargets={},savedConsultation=null,sourcePicker=null,conflictDialog=null,initialGeneration=null,refinement=null;
const consultation=()=>({instruction:$('ai-instruction').value,targets:copy(aiTargets)});
function restoreConsultation(w){$('ai-instruction').value=w?.consultation?.instruction||'';aiTargets=copy(w?.consultation?.targets||{});savedConsultation=fp(consultation());}
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function button(text,fn,kind='secondary'){const b=node('button',text,'ui-button ui-button--'+kind);b.type='button';b.addEventListener('click',fn);return b;}
function hint(text){return node('p',text,'ui-hint');}
function field(host,label,value,onInput,{options=null,type='text',rows=2,disabled=false}={}){const box=node('label',undefined,'ui-field');box.append(node('span',label));const e=node(options?'select':type==='textarea'?'textarea':'input');if(options){for(const o of options){const [v,t]=Array.isArray(o)?o:[o,o];const opt=node('option',t);opt.value=v;box; e.append(opt);}}else if(type==='textarea')e.rows=rows;else e.type=type;e.value=value??'';e.dataset.cwLocked=String(disabled);e.disabled=disabled; e.addEventListener(options?'change':'input',()=>onInput(e.value));box.append(e);host.append(box);return e;}
const workflow=()=>({version:1,step:S.step,category:S.category,selected:S.selected,consultation:consultation()});
const authoring=()=>M.authoring(S.authoring,S.prep.genre,S.prep.storyType);
function savedStage(){const w=S.kind==='native'?S.native?.buffer?.workflow:S.originalContent?.workflow;return w?.version===1&&[1,2,3].includes(w.step)?['','① 下書きを用意','② 内容を整える','③ 確認して採用'][w.step]:'未記録';}
function content(){if(S.kind==='native')return {...copy(S.buffer),workflow:workflow()};return {...copy(S.originalContent),version:1,name:S.prep.name,overview:S.prep.overview,time:M.workspaceTime(S.draft,S.originalContent.time),draft_text:S.raw,source_draft_id:S.proposal?.request_id||null,source_revision:S.proposal?.edit_revision||S.proposal?.revision||null,authoring:authoring(),workflow:workflow()};}
function semantic(c=content()){const d=copy(c);delete d.workflow;delete d.source_draft_id;delete d.source_revision;if(typeof d.draft_text==='string')try{d.draft_text=JSON.parse(d.draft_text);}catch(_){}return fp(d);}
function dirty(){if(savedConsultation!==null&&fp(consultation())!==savedConsultation)return true;return S.saved===null?!!(Object.values(S.prep).some(v=>v!==''&&v!==null)||S.raw||$('ai-instruction').value||S.kind==='native'):semantic()!==S.saved;}
function message(text){$('notice').textContent=text;}
function changed(){S.checked=null;S.published=null;$('consent').checked=false;if(S.kind==='new'&&S.draft)S.raw=JSON.stringify(S.draft,null,2);controls();}
function locked(el,value){el.dataset.cwLocked=String(value);}
function guard(){if(root.querySelector('dialog[open]'))return false;if(S.refinementGate||S.generationGate||S.busy||S.unknown||S.conflict)return false;return window.WorldBloomDraftForms?.canLeave()!==false;}
function publishedNative(){return S.kind==='native'&&(S.native?.published_world||S.published)||null;}
function readyToWrite(){if(publishedNative()){message('作成済みの取込下書きは閲覧専用です。作成した世界の設定を開いてください。');return false;}if(root.querySelector('dialog[open]:not([data-cw-refinement-dialog])'))return false;if(S.busy||S.unknown||S.conflict)return false;if(window.WorldBloomDraftForms?.hasPending()){message('詳細フォームの変更を「下書きに反映」するか、取消してから保存・確認してください。');return false;}return true;}
function controls(){
 const readOnly=!!publishedNative(),viewOnly=readOnly&&S.screen==='workflow';root.dataset.cwReadonlyView=String(viewOnly);
 for(const e of root.querySelectorAll('input,select,textarea,button')){if(e.closest?.('[data-cw-conflict-dialog],[data-cw-generation-dialog],[data-cw-refinement-dialog],[data-cw-refinement-launcher]'))continue;e.disabled=viewOnly&&!e.matches?.('[data-cw-help]')&&!!e.closest?.('[data-cw-prepare],[data-cw-fields],[data-cw-ai],[data-cw-new-tools],[data-cw-panel="3"]')||S.generationGate||S.refinementGate||S.busy||S.unknown||S.conflict||e.dataset.cwLocked==='true'||e.dataset.nativeLocked==='true';}
 $('recovery').hidden=!(S.unknown||S.conflict);$('recovery-note').textContent=S.conflict?'別の画面で下書きが更新されています。現在の入力は保持しています。保存済みの結果を読み取り確認できます。':'通信結果を確定できません。入力を保持し、自動再送はしていません。';$('save-check').disabled=S.busy||!(S.unknown||S.conflict)||!S.pending;
 $('compare').hidden=!S.conflict||!S.active||!S.id;$('compare').disabled=S.busy||!S.conflict||!S.active||!S.id;
 $('mode-note').hidden=viewOnly;$('edit-note').textContent=readOnly?'項目を選んで保存済みの内容を閲覧できます。':'項目を選んで編集できます。AIへの相談は任意です。';
 $('readonly').hidden=!viewOnly;$('created-world').href=readOnly?'/worlds/'+encodeURIComponent(publishedNative())+'?mode=create':'#';$('start-existing').href=readOnly?'/worlds/new?mode=create&screen=world-import&from='+encodeURIComponent(publishedNative()):'#';$('ai-instruction').closest?.('[data-cw-ai]')?.toggleAttribute('hidden',readOnly);
 $('save').hidden=S.screen!=='workflow'||readOnly;$('saved').hidden=S.screen!=='workflow';$('continue').hidden=!S.active;$('continue').textContent=readOnly?'閲覧中の下書きへ戻る':'編集中の下書きへ戻る';root.querySelector('.ui-footer').hidden=viewOnly||!['workflow','world-import'].includes(S.screen);$('home').hidden=S.screen==='entry';$('home').setAttribute('aria-disabled',String(S.busy||S.unknown||S.conflict));
 $('held').hidden=!S.active;$('held').textContent=S.active?(readOnly?'作成済みの取込下書きを保持しています。「閲覧中の下書きへ戻る」で内容を確認できます。':'編集中の下書きを保持しています。「編集中の下書きへ戻る」で続けられます。画面の移動では保存しません。'):'';
 $('import').hidden=S.screen!=='world-import';$('world-picked').hidden=S.screen!=='world-import';sourcePicker?.sync();
 $('import').disabled||=!$('source').value;$('import-zip').disabled||=!$('zip').files?.length;
 $('refresh').disabled||=listBusy||resuming;for(const b of root.querySelectorAll('[data-cw-resume]'))b.disabled||=listBusy||resuming;
 $('ai-cancel').disabled=!S.aiId||S.aiCancelled; $('ai-check').disabled=S.busy||!S.aiId;
 $('saved').textContent=S.busy?'処理中・入力を保持しています':S.unknown?'結果が不明です。再送せず上の通知から確認':S.conflict?'版の競合があります。入力は保持しています':!S.active?'まだ下書きを選択していません':dirty()?'● 下書き内容に未保存の変更があります':S.saved!==null?'● 下書き内容を保存済み（再開位置：'+savedStage()+'）':'まだ下書きを保存していません';
 $('next').hidden=S.screen!=='workflow'||readOnly;
 $('next').textContent=S.screen==='entry'?'編集中の下書きへ戻る':S.published&&S.step===3?'保存した世界を開く →':S.step===1?'内容を整える →':S.step===2?'確認へ進む →':'この内容で世界を作成';
 if(S.screen==='workflow'&&S.step===3&&!S.published)$('next').disabled||=(S.kind==='native'&&!!S.native.published_world)||!S.checked||S.checked.signature!==semantic()||!$('consent').checked;
 $('ai-propose').disabled||=!$('ai-target').value||!$('ai-instruction').value.trim()||!!S.aiId||initialGeneration?.active()||['waiting','mock'].includes(root.dataset.mode);
 $('ai-apply').disabled||=!S.ai||S.ai.state!=='ready'||S.aiBase!==semantic()||S.aiCancelled;
 $('generate').disabled||=S.kind==='native'||initialGeneration?.active()||root.dataset.mode==='waiting';$('save').disabled||=readOnly;$('ai-propose').disabled||=readOnly;$('ai-apply').disabled||=readOnly;$('ai-cancel').disabled||=readOnly;$('ai-check').disabled||=readOnly;$('check').disabled||=readOnly;$('consent').disabled||=readOnly;$('check-actions').hidden=readOnly;$('consent-row').hidden=readOnly;
 $('new-tools').hidden=S.kind==='native';
 $('detail-editor').disabled||=!S.active||S.kind==='native'||!S.draft;$('json').disabled||=!S.active||S.kind==='native';$('json-apply').disabled||=!S.active||S.kind==='native';
 for(const e of $('prepare').elements)if(!e.matches?.('[data-cw-help]'))e.disabled||=S.kind==='native';
 refinement?.sync();
}
function carry(){const u=new URL(location.href);u.searchParams.delete('workspace');u.searchParams.delete('native_draft');u.searchParams.delete('draft');if(S.id)u.searchParams.set(S.kind==='native'?'native_draft':'workspace',S.id);if(S.proposal?.request_id)u.searchParams.set('draft',S.proposal.request_id);if(S.aiId)u.searchParams.set('cw_ai',S.aiId);else u.searchParams.delete('cw_ai');u.searchParams.set('screen',S.screen);if(S.screen==='workflow')u.searchParams.set('step',S.step);else u.searchParams.delete('step');history.replaceState({...history.state,cwIndex:historyIndex,cwStep:S.step,cwScreen:S.screen},'',u.pathname+u.search+u.hash);const home=new URL(u);home.searchParams.set('screen','entry');home.searchParams.delete('step');$('home').href=home.pathname+home.search;}
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});let v;try{v=await r.json();}catch(_){throw Error('通信結果を読み取れません');}if(!r.ok){const e=Error(v.message||'処理を確認できません');e.known=r.status<500;e.status=r.status;e.fields=v.field_errors;throw e;}return v;}
async function run(action,fn){if(S.busy||S.unknown||S.conflict)return false;S.busy=true;$('error').textContent='';controls();try{await fn();S.pending=null;return true;}catch(e){S.unknown=!e.known;S.conflict=e.status===409;$('error').textContent=e.fields?Object.entries(e.fields).map(([k,v])=>k+'：'+v).join('\n'):e.message;if(S.unknown)message('結果が不明です。自動再送はしません。上の通知の「送信した処理の結果を確認」、またはAI欄の「提案の状態を確認」で読取確認してください。');return false;}finally{S.busy=false;if(S.aiCancelled&&!S.unknown&&!S.conflict){S.aiId=null;carry();}controls();}}
function syncPrepare(){for(const [k,v]of Object.entries(S.prep)){if(k==='storyType'){for(const radio of $('prepare').querySelectorAll('[name="storyType"]'))radio.checked=radio.value===v;const unknown=!['','threat-response'].includes(v);$('old-type').hidden=!unknown;$('old-type').textContent=unknown?'保存済みの手がかりを保持しています：'+v:'';continue;}const input=$('prepare').elements[k];if(!input)continue;if(input.tagName==='SELECT'&&v&&![...input.options].some(o=>o.value===v)){const opt=node('option','保存済みの値：'+v);opt.value=v;input.append(opt);}input.value=v;}}
function ensureDraft(){if(S.kind==='native'||S.draft)return;if(S.raw){try{S.draft=JSON.parse(S.raw);}catch(_){return;}}else S.draft=M.empty(S.prep.name,S.prep.overview);M.hydrateTiming(S.draft,S.originalContent.time);syncIdentity();S.raw=JSON.stringify(S.draft,null,2);}
function syncIdentity(){if(!S.draft||typeof S.draft!=='object'||Array.isArray(S.draft))return;S.draft.name=S.prep.name;S.draft.overview=S.prep.overview;}
function pushNavigation(){historyIndex++;history.pushState({...history.state,cwIndex:historyIndex,cwStep:S.step,cwScreen:S.screen},'',location.href);carry();}
function navigateScreen(screen,push=true){if(!guard())return false;if(screen==='import')screen='world-import';const changedScreen=S.screen!==screen;S.screen=screen;if(push&&changedScreen)pushNavigation();render();if(screen==='resume')refresh().catch(()=>message('一覧を取得できません。「一覧を更新」で確認してください。'));return true;}
function navigate(step,push=true,category=null){if(!guard())return false;const moved=S.screen!=='workflow'||S.step!==step||!!category;S.screen='workflow';S.active=true;if(step>1)ensureDraft();S.step=step;if(category){S.category=category;S.selected=0;}if(push&&moved)pushNavigation();render();return true;}
async function beginNew(){if(!await canSwitch())return;Object.assign(S,{screen:'workflow',active:true,step:1,category:'people',selected:0,kind:'new',draft:null,raw:'',originalContent:{},authoring:null,prep:{name:'',overview:'',genre:'',storyType:''},id:null,revision:null,saved:null,proposal:null,native:null,buffer:null,checked:null,pending:null,ai:null,aiId:null,aiBase:null,aiCancelled:false,published:null});++S.epoch;aiTargets={};savedConsultation=null;$('ai-instruction').value='';$('ai-status').textContent='';$('consent').checked=false;$('error').textContent='';message('新しい下書きを用意します。まだ保存・AI実行はしていません。');pushNavigation();render();}
function render(){
 for(const p of root.querySelectorAll('[data-cw-screen]'))p.hidden=p.dataset.cwScreen!==S.screen;
 $('workflow').hidden=S.screen!=='workflow';$('steps').hidden=S.screen!=='workflow';root.dataset.cwView=S.screen;root.querySelector('.ui-heading h1').textContent=S.screen==='resume'?'保存した下書きを再開':'新しい世界を作成';root.querySelector('.ui-heading p').textContent=S.screen==='entry'?'始め方を選んでください。':S.screen==='world-import'?'既存の世界から下書きを作ります。':S.screen==='zip-import'?'ZIPから下書きを作ります。':S.screen==='resume'?'下書きを選んで、保存した段階から編集を続けます。':'設定を3つのステップで整えます。';
 for(const p of root.querySelectorAll('[data-cw-panel]'))p.hidden=Number(p.dataset.cwPanel)!==S.step;
 for(const b of root.querySelectorAll('[data-cw-step]')){if(Number(b.dataset.cwStep)===S.step)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');}
 syncPrepare();$('title').textContent=S.kind==='native'?S.native.name:S.prep.name||'名前はまだ未設定です';
 $('categories').replaceChildren();for(const [key,label]of categories){const b=button(label,()=>{if(!guard())return;S.category=key;S.selected=0;for(const item of $('categories').children){if(item.textContent===label)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');}renderFields();renderAI();controls();});if(key===S.category)b.setAttribute('aria-current','page');$('categories').append(b);}
 if(S.screen==='workflow'&&S.step===2){renderFields();renderAI();}if(S.screen==='workflow'&&S.step===3)renderSummary();$('json').value=S.raw;controls();$('scroll');root.querySelector('.cw-scroll').scrollTop=0;
}
function basicFields(){const host=$('fields'),d=S.draft;if(!d||!Array.isArray(d.people)||!Array.isArray(d.places)||!Array.isArray(d.items)){host.append(hint('この下書きは未完成かJSON形式を確認する必要があります。②の「詳細編集」にある下案JSONで修復できます。入力は保持しています。'));return;}
 const kind=S.category,rows=d[kind];host.append(node('h2',categories.find(c=>c[0]===kind)[1]));const chips=node('div',undefined,'cw-chips');rows.forEach((r,i)=>{const b=button(r.name||'名前未設定 '+(i+1),()=>{if(!guard())return;S.selected=i;renderFields();renderAI();controls();});b.setAttribute('aria-pressed',String(i===S.selected));chips.append(b);});const max=8;const add=button('＋ '+({people:'人物',places:'場所',items:'持ち物'}[kind])+'を追加',()=>{if(!guard())return;if(rows.length>=max)return;rows.push(kind==='people'?{name:'',description:'',personality:'',entry:'',goal:{description:'',conditions:[]},relations:[]}:kind==='places'?{name:'',description:''}:{name:'',description:'',holders:[]});S.selected=rows.length-1;changed();renderFields();renderAI();controls();});locked(add,rows.length>=max);chips.append(add);host.append(chips);const p=rows[S.selected]||rows[0];if(!p){host.append(hint('まだ登録されていません。追加ボタンから作成できます。'));return;}
 S.selected=rows.indexOf(p);field(host,'名前',p.name,v=>{M.rename(d,kind,S.selected,v);changed();const chips=[...host.querySelectorAll('.cw-chips button')];rows.forEach((r,i)=>{if(chips[i])chips[i].textContent=r.name||'名前未設定 '+(i+1);});if(kind==='people')for(const select of host.querySelectorAll('select')){const label=select.closest('label')?.querySelector('span')?.textContent;const key=label==='主人公'?'protagonist':label==='相手役'?'antagonist':null;if(key){select.replaceChildren();for(const value of ['',...d.people.map(x=>x.name).filter(Boolean)]){const o=node('option',value||'未設定');o.value=value;select.append(o);}select.value=d[key];}}renderAI();controls();});
 if(kind==='people'){
  field(host,'人物の紹介',p.description,v=>{p.description=v;changed();},{type:'textarea'});
  field(host,'人物像・役割の説明',p.personality,v=>{p.personality=v;changed();});
  field(host,'目標（文章）',p.goal?.description??window.WorldBloomDraftGoals.goalValue(p.goal)?.description??'',v=>{p.goal=window.WorldBloomDraftGoals.goalValue(p.goal)||{conditions:[]};p.goal.description=v;changed();},{type:'textarea'});host.append(hint('達成条件・関係・導入文は②本文の「詳細編集」から編集できます。文章から達成条件を推測しません。'));
  field(host,'開始位置',p.entry,v=>{p.entry=v;changed();},{options:[['','未設定'],...d.places.map(x=>x.name).filter(Boolean)]});
  const more=node('details');more.append(node('summary','詳細設定'));host.append(more);
  const personNames=d.people.map(x=>x.name).filter(Boolean);for(const [key,label]of [['protagonist','主人公'],['antagonist','相手役']])field(more,label,d[key],v=>{d[key]=v;changed();},{options:[['','未設定'],...personNames]});
  more.append(hint('文章の目標と、優先事項の条件・優先度は別です。文章から推測して補いません。名前変更後は関連する設定の検証も必要です。'));
  const advanced=node('div');more.append(button('優先事項のフォームを開く',()=>{advanced.replaceChildren();mountForms(advanced,'people');}),advanced);
 }else if(kind==='places'){
  field(host,'場所の説明',p.description,v=>{p.description=v;changed();},{type:'textarea'});
  const more=node('details');more.append(node('summary','経路の詳細'));const routeAdd=button('経路を追加',()=>{d.routes.push({from:p.name,to:'',cost:1});changed();renderFields();controls();});more.append(routeAdd);for(const route of d.routes){const row=node('fieldset');for(const [key,label]of [['from','出発地'],['to','到着地']])field(row,label,route[key],v=>{route[key]=v;changed();},{options:[['','未設定'],...d.places.map(x=>x.name).filter(Boolean)]});field(row,'移動コスト',route.cost??1,v=>{route.cost=Number(v);changed();},{type:'number'});more.append(row);}host.append(more);
 }else{
  field(host,'持ち物の説明',p.description||'',v=>{p.description=v;changed();},{type:'textarea'});
  host.append(hint('所持者・数量は複数指定できます。未設定のまま保存でき、採用前に内容を確認します。'));
  if(!p.holders){host.append(hint('既存の所持者形式を保持しています：'+(p.holder||'未設定')));field(host,'所持者',p.holder||'',v=>{p.holder=v;changed();},{options:[['','未設定'],...d.people.map(x=>x.name).filter(Boolean)]});}
  else{for(const holder of p.holders){const row=node('fieldset');field(row,'所持者',holder.person,v=>{holder.person=v;changed();},{options:[['','未設定'],...d.people.map(x=>x.name).filter(Boolean)]});field(row,'個数',holder.count,v=>{holder.count=Number(v);changed();},{type:'number'});host.append(row);}host.append(button('所持者を追加',()=>{p.holders.push({person:'',count:1});changed();renderFields();controls();}));}
 }
}
function mountForms(host,section){if(!S.draft||M.report(S.draft).missing.length||S.draft.people.some(p=>!p.name||!p.entry)){host.append(hint('人物2人以上と場所などの基本情報を入力すると詳細フォームを確認できます。途中の入力は下書き保存できます。'));return;}window.WorldBloomDraftForms.mount(host,{draft:copy(S.draft),section,getDraft:()=>S.draft,onApply:d=>{S.draft=d;S.raw=JSON.stringify(d,null,2);changed();renderFields();controls();}});}
function generationNotes(){
 const data=S.originalContent.generation||window.WorldBloomCreationGeneration.metadata(S.proposal||{}),host=$('fields');
 if(!data?.goal_plan_notes)return;const notes=data.goal_plan_notes,box=node('details',undefined,'ui-notice');box.append(node('summary','AIの目的解釈・補完を確認'));box.append(hint(notes.notice||'AIが解釈した案です。原文の引用・本人の確認済みの事実とは限りません。'));
 for(const row of notes.notes||[])box.append(hint(row.actor+'：'+row.text+'（'+(row.kind==='same_wording'?'概要と同じ表現':'AIの解釈・補完')+'）'));
 for(const text of notes.assumptions||[])box.append(hint('補完：'+(typeof text==='string'?text:JSON.stringify(text))));host.append(box);
}
function renderFields(){formsEditor=null;const host=$('fields');host.replaceChildren();if(S.kind==='native'){
  const model=S.native.model;if(!model){host.append(hint(S.native.parse_error+' 原本は保持し、編集・採用は停止しています。'));return;}
  if(['places','items'].includes(S.category)){host.append(node('h2',categories.find(c=>c[0]===S.category)[1]),hint('この取り込み下書きでは、この項目の編集は未対応です。原本と未対応の設定を保持し、削除・変換しません。'),node('p',(model[S.category]||[]).join('・')||'登録なし'));return;}
  if(S.category==='actions')host.append(hint(publishedNative()?'保存済みの行動の人物・時間帯制限を表示しています。原本は変更しません。':'編集できるのは行動の人物・時間帯制限です。その他のルールは原本のまま保持します。'));
  const box=node('div');host.append(box);formsEditor=new window.WorldBloomDraftForms.Editor(box,{model,edits:S.buffer.edits,native:true,onChange:edits=>{const kinds=S.category==='people'?['goal','commitments']:S.category==='actions'?['action']:['time'];S.buffer.edits=[...S.buffer.edits.filter(e=>!kinds.includes(e.kind)),...edits.filter(e=>kinds.includes(e.kind))];changed();}});formsEditor.render(S.category==='people'?'people':S.category==='actions'?'actions':'time');
  if(S.category==='time'){const ev=node('details');ev.append(node('summary','予定された出来事'));const eb=node('div');ev.append(eb);host.append(ev);const eventEditor=new window.WorldBloomDraftForms.Editor(eb,{model,edits:S.buffer.edits,native:true,onChange:edits=>{S.buffer.edits=mergeEdits(S.buffer.edits,edits,'event');changed();}});eventEditor.render('events');}
  return;
 }
 generationNotes();
 if(['people','places','items'].includes(S.category)){basicFields();return;}
 host.append(node('h2',categories.find(c=>c[0]===S.category)[1]));if(!S.draft){host.append(hint('下案JSONを確認してください。'));return;}
 if(S.category==='time'){const t=node('div');host.append(t);window.WorldBloomDraftForms.mountTime(t,{draft:copy(S.draft),getDraft:()=>S.draft,onApply:d=>{S.draft=d;changed();renderFields();controls();}});const ev=node('details');ev.append(node('summary','予定された出来事'));const e=node('div');ev.append(e);host.append(ev);mountForms(e,'events');}
 else{const a=node('div');host.append(a);mountForms(a,'actions');host.append(hint('世界のルールは既存の初期設定を使います。高度な条件・対処等は下案JSONで保持・編集できます。未指定のルールを概要の文章から作りません。'));}
}
function mergeEdits(current,next,kind){return [...current.filter(e=>e.kind!==kind),...next.filter(e=>e.kind===kind)];}
function nativeTargets(){const m=S.native?.model;if(!m)return [];if(S.category==='time')return [[{kind:'time',target:null},'時間'],...m.scheduled_events.map((e,i)=>[{kind:'event',target:i},'予定：'+(e.label||e.id)])];if(S.category==='people')return m.people.flatMap(p=>[[{kind:'goal',target:p.name},p.name+'：目標'],[{kind:'commitments',target:p.name},p.name+'：優先事項']]);if(S.category==='actions')return m.actions.filter(x=>x!=='verb:rest').map(x=>[{kind:'action',target:x},m.action_labels[x]||x]);return [];}
function newTargets(){const d=S.draft;if(!d)return [];if(S.category==='people')return d.people.filter(p=>p.name).flatMap(p=>[[{section:'people',entity:p.name,field:'profile'},p.name+'：紹介・人物像・開始位置'],[{section:'people',entity:p.name,field:'goal'},p.name+'：目標']]);if(S.category==='places')return d.places.filter(p=>p.name).map(p=>[{section:'places',entity:p.name,field:'description'},p.name+'：説明']);if(S.category==='items')return d.items.filter(p=>p.name).map(p=>[{section:'items',entity:p.name,field:'holder'},p.name+'：初期所持']);return [];}
function changeLabel(c){const labels={days:'期間（日）',slots:'実行できる時間帯',actors:'実行できる人物',description:'説明',personality:'人物像・役割の説明',entry:'開始位置',goal:'目標',holder:'所持者',holders:'所持者と個数',commitments:'優先事項',initial_story:'導入文'};if(c.field)return labels[c.field]||c.field;const parts=(c.path||'').split('/').slice(1),entity=S.draft?.[parts[0]]?.[Number(parts[1])]?.name;return (entity?entity+'：':'')+(labels[parts[2]||parts[0]]||'選択した項目の変更');}
function renderAI(){const targets=S.kind==='native'?nativeTargets():newTargets(),select=$('ai-target');select.replaceChildren();for(const [value,label]of targets){const o=node('option',label);o.value=JSON.stringify(value);select.append(o);}const preferred=aiTargets[S.category];if(preferred&&targets.some(([t])=>JSON.stringify(t)===preferred))select.value=preferred;else if(S.category==='people'&&S.kind==='new'&&S.draft?.people[S.selected]?.name){const match=targets.find(([t])=>t.entity===S.draft.people[S.selected].name);if(match)select.value=JSON.stringify(match[0]);}locked(select,!targets.length);$('ai-note').textContent=!targets.length?(S.kind==='native'&&(!S.native.model||['places','items'].includes(S.category))?'この項目の手入力・AI修正は未対応です。取り込んだ原本を保持しています。':S.kind==='new'&&['people','places','items'].includes(S.category)?'対象を登録すると、その項目をAIへ相談できます。':'この項目のAI修正は未対応です。手入力で編集できます。'):root.dataset.mode==='fixture'?'固定応答で候補と差分を検証します。実AI・GPUは呼びません。':'選んだ項目と登録済みの参照をAIへ送り、修正候補を1回作ります。手修正を先に下書き保存してください。';$('diff-content').replaceChildren();if(S.ai?.errors)for(const [key,value]of Object.entries(S.ai.errors))$('diff-content').append(hint(key+'：'+value));if(S.ai?.changes?.length){for(const c of S.ai.changes){const box=node('section');box.append(node('h3',changeLabel(c)),node('pre','変更前\n'+JSON.stringify(c.before,null,2)),node('pre','提案\n'+JSON.stringify(c.after,null,2)));$('diff-content').append(box);}}$('diff').open=!!(S.ai?.changes?.length||Object.keys(S.ai?.errors||{}).length);}
function summaryGroup(host,title,items,category){const box=node('div');box.append(node('h3',title+(category==='time'?'':'（'+items.length+'）')),node('p',items.length?(items.slice(0,12).join('・')+(items.length>12?' ほか'+(items.length-12)+'件':'')):'未設定'),button('編集',()=>navigate(2,true,category)));host.append(box);}
function renderSummary(){$('review-title').textContent=publishedNative()?'作成済みの内容を確認':'この内容で新しい世界を作成します';const host=$('summary');host.replaceChildren();$('defaults').replaceChildren();const d=S.kind==='native'?(S.native.model?window.WorldBloomDraftForms.effective(S.native.model,S.buffer.edits):null):S.draft;host.append(node('h2',S.kind==='native'?S.native.name:S.prep.name||'名前未設定'),node('p',S.kind==='native'?'取込原本 '+S.native.file_count+'ファイルを保持。元世界は変更しません。':S.prep.overview));const grid=node('div',undefined,'cw-review-grid');host.append(grid);
 summaryGroup(grid,'人物',(d?.people||[]).map(p=>p.name),'people');summaryGroup(grid,'場所',S.kind==='native'?d?.places||[]:(d?.places||[]).map(p=>p.name),'places');summaryGroup(grid,'持ち物',S.kind==='native'?d?.items||[]:(d?.items||[]).map(p=>p.name),'items');summaryGroup(grid,'時間・行動',S.kind==='native'?[d?.time?'期間 '+d.time.days+'日間・'+(Array.isArray(d.time.slots)?d.time.slots.join('／'):d.time.slots):'原本の時間設定を保持']:M.report(d||M.empty()).defaults.slice(0,1),'time');
 if(S.kind==='native'&&S.buffer.edits.length){const changes=node('details');changes.append(node('summary','今回の変更（'+S.buffer.edits.length+'件）'));for(const edit of S.buffer.edits){const labels={time:'期間・時間帯',event:'予定',action:'行動の人物・時間制限',goal:'人物の目的',commitments:'人物の優先事項'};changes.append(node('h3',(labels[edit.kind]||edit.kind)+'：'+(d?.action_labels?.[edit.target]||edit.target||'世界')),node('pre',JSON.stringify(edit.values,null,2)));}host.append(changes);}
 const notes=S.kind==='native'?['場所・持ち物・世界ルール等の未対応項目は原本を保持します。','期間・予定・人物の目的/優先事項・行動の人物/時間帯制限が編集対象です。']:S.draft?[...M.report(S.draft).missing.map(x=>'未設定：'+x),...M.report(S.draft).defaults]:['下案JSONを確認してください。'];for(const text of notes)$('defaults').append(node('p',text));
 $('validation').textContent=S.kind==='native'&&S.native.published_world&&!S.published?'この下書きから作成した世界が既にあります。下書きを保存しても作成済みの世界は更新されません。':S.published?'新しい世界として保存済みです。物語の実行は開始していません。':S.checked&&S.checked.signature===semantic()?S.checked.message:'まだ現在の内容を確認していません。設定と実行入力を確認してください。';$('check').textContent=S.kind==='new'&&S.proposal?.mode==='live'?'AI整合性を含め設定を確認（AIを呼びます）':'設定と実行入力を確認（AIは呼びません）';
 if(S.published){const a=node('a','保存した世界設定を開く →');a.href='/worlds/'+encodeURIComponent(S.published);$('result').replaceChildren(a);}else $('result').replaceChildren();
}
async function saveDraft(){if(!S.active||!readyToWrite())return false;return run('save',async()=>{
 if(S.kind==='native'){const c=content();S.pending={action:'save',kind:'native',id:S.id,content:c};const r=await api('/api/native-world-drafts/'+S.id+'/save',{revision:S.native.revision,request_id:uuid(),buffer:c});S.native=r;S.buffer=copy(r.buffer);S.saved=semantic(r.buffer);S.checked=null;}
 else{if(!S.id)S.id=uuid();const c=content();S.pending={action:'save',kind:'new',id:S.id,content:c};carry();const r=await api('/api/world-draft-workspaces/'+S.id,{revision:S.revision,request_id:uuid(),content:c});S.revision=r.revision;S.originalContent=copy(r.content);S.saved=semantic(r.content);}
 savedConsultation=fp(consultation());initialGeneration?.saved();message('下書きを保存しました。未完成の入力を保持し、世界の採用・AI実行は行っていません。');carry();
 });}
async function ensureProposal(){if(S.proposal&&(S.proposal.edit_revision||S.proposal.revision)&&S.proposal.name===S.prep.name&&S.proposal.overview===S.prep.overview&&fp(S.proposal.authoring||null)===fp(authoring())&&!['adopted','cancelled'].includes(S.proposal.state))return;const keepLive=S.proposal?.mode==='live',id=uuid();S.proposal={request_id:id,name:S.prep.name,overview:S.prep.overview};S.pending={action:'stage',id};carry();S.proposal=await api('/api/world-drafts/'+id+'/stage',{name:S.prep.name,overview:S.prep.overview,draft:S.draft,authoring:authoring(),...(keepLive?{}:{validation_mode:'structural'})});}
async function check(){if(!readyToWrite())return;if(dirty()||!S.id){message('先に下書きを保存してください。確認画面でも「下書きを保存」を使えます。');return;}await run('check',async()=>{
 if(S.kind==='native'){S.pending={action:'check',kind:'native',id:S.id};const r=await api('/api/native-world-drafts/'+S.id+'/check',{revision:S.native.revision});S.checked={signature:semantic(),digest:r.digest,message:r.note+' '+(r.warnings||[]).map(w=>w.message).join(' / ')};}
 else{if(!S.draft)throw Object.assign(Error('下案JSONを確認してください'),{known:true});await ensureProposal();const id=S.proposal.request_id;S.pending={action:'check',kind:'new',id,content:copy(S.draft)};S.proposal=await api('/api/world-drafts/'+id+'/validate',{revision:S.proposal.edit_revision||S.proposal.revision,draft:S.draft});S.draft=copy(S.proposal.draft);S.raw=JSON.stringify(S.draft,null,2);S.checked={signature:semantic(),message:S.proposal.mode==='live'?'構造・参照・実行入力とAI整合性を確認しました。内容を確認して採用してください。':'構造・参照・実行入力を確認しました。AIによる内容の整合性確認は実施していません。'};}
 $('consent').checked=false;renderSummary();
 });}
async function publish(){if(!readyToWrite()||!S.checked||S.checked.signature!==semantic()||!$('consent').checked||S.published)return;await run('publish',async()=>{S.pending={action:'publish',kind:S.kind,id:S.kind==='native'?S.id:S.proposal.request_id};const r=S.kind==='native'?await api('/api/native-world-drafts/'+S.id+'/publish',{revision:S.native.revision,digest:S.checked.digest}):await api('/api/world-drafts/'+S.proposal.request_id+'/adopt',{revision:S.proposal.revision});S.published=r.world_id;if(S.proposal)S.proposal.state='adopted';message('新しい世界として保存しました。物語の実行は開始していません。');renderSummary();});}
let listEpoch=0,listBusy=false,resuming=false;
sourcePicker=window.WorldBloomWorldPicker.mount(root.querySelector('[data-cw-world-picker]'),controls);
async function refresh(){const epoch=++listEpoch;listBusy=true;$('list').setAttribute('aria-busy','true');$('list-status').textContent='一覧を読み込んでいます。';$('list-count').textContent='取得中';controls();try{const values=await Promise.all([api('/api/world-draft-workspaces'),api('/api/native-world-drafts')]);if(epoch!==listEpoch)return;const rows=window.WorldBloomSavedDrafts.normalize(values);window.WorldBloomSavedDrafts.render($('list'),rows,openSaved,document);$('list-count').textContent=rows.length+'件';$('list-status').textContent=rows.length?'':'保存した下書きはまだありません。';}catch(e){if(epoch!==listEpoch)return;$('list').replaceChildren();$('list-count').textContent='件数未確認';$('list-status').textContent='一覧を取得できませんでした。「一覧を更新」で再試行できます。';}finally{if(epoch===listEpoch){listBusy=false;$('list').setAttribute('aria-busy','false');controls();}}}
async function openSaved(kind,id){if(resuming||listBusy||!guard())return;resuming=true;controls();try{if(!await canSwitch())return;await run('open',async()=>{if(kind==='native')loadNative(await api('/api/native-world-drafts/'+id));else await loadNew(await api('/api/world-draft-workspaces/'+id));});}finally{resuming=false;controls();}}
async function loadNew(r,screen='workflow'){const c=r.content;if(c.version!==1)throw Object.assign(Error('未対応の下書き形式です。入力を保持します'),{known:true});let proposal=null;if(c.source_draft_id){try{const p=await api('/api/world-drafts/'+c.source_draft_id);if(c.source_revision===(p.edit_revision||p.revision)&&fp(c.authoring||null)===fp(p.authoring||null)&&!['adopted','cancelled'].includes(p.state))proposal=p;}catch(e){if(e.status!==404)throw e;}}
 S.screen=screen;S.active=true;S.kind='new';S.id=r.workspace_id;S.revision=r.revision;S.originalContent=copy(c);S.authoring=copy(c.authoring||null);S.raw=c.draft_text;try{S.draft=c.draft_text?JSON.parse(c.draft_text):null;if(S.draft){M.hydrateTiming(S.draft,c.time);S.raw=JSON.stringify(S.draft,null,2);}}catch(_){S.draft=null;}S.proposal=proposal;S.native=null;S.buffer=null;S.prep={name:c.name,overview:c.overview,genre:c.authoring?.outline?.genre||'',storyType:c.authoring?.type||''};const w=c.workflow;restoreConsultation(w);S.step=w?.step|| (S.raw?2:1);S.category=w?.category||'people';S.selected=w?.selected||0;S.checked=null;S.published=null;$('consent').checked=false;S.saved=semantic();S.unknown=S.conflict=false;S.ai=S.aiId=null;carry();render();message('下書きを再開しました。検証・AI・採用は自動実行していません。');}
function loadNative(r,screen='workflow'){S.screen=screen;S.active=true;S.kind='native';S.native=r;S.buffer=copy(r.buffer);S.id=r.workspace_id;S.revision=null;S.proposal=null;S.prep={name:r.name,overview:'元の設定・未対応ファイルを保持した下書き',genre:'',storyType:''};const w=r.buffer.workflow;restoreConsultation(w);S.step=w?.step||2;S.category=w?.category||'time';S.selected=w?.selected||0;S.checked=null;S.published=r.published_world||null;$('consent').checked=false;S.saved=semantic();S.unknown=S.conflict=false;S.ai=S.aiId=null;carry();render();message('取込下書きを読み込みました。元の世界は変更していません。');}
async function canSwitch(){if(!guard())return false;if(dirty()){if(!window.confirm('現在の入力を下書きに保存してから、別の下書きへ切り替えますか？'))return false;return saveDraft();}return true;}
async function importSource(source){if(!await canSwitch())return;await run('import',async()=>{const id=uuid();S.pending={action:'import',kind:'native',id};const r=await api('/api/native-world-drafts/'+id+'/create',{request_id:uuid(),source});loadNative(r);});}
async function recover(){if(S.busy)return;const pending=S.pending;if(!pending){message('確認できる送信記録がありません。現在の入力は保持しています。自動再送や再読込は行っていません。');return;}S.busy=true;controls();try{
 const r=await api(pending.kind==='native'?'/api/native-world-drafts/'+pending.id:['save','resolve'].includes(pending.action)?'/api/world-draft-workspaces/'+pending.id:'/api/world-drafts/'+pending.id);
 if(pending.action==='save'&&fp(pending.kind==='native'?r.buffer:r.content)===fp(pending.content)){if(pending.kind==='native')S.native=r;else{S.revision=r.revision;S.originalContent=copy(r.content);}S.saved=semantic(pending.content);savedConsultation=fp(pending.content.workflow?.consultation||{instruction:'',targets:{}});S.unknown=S.conflict=false;message('送信した版の保存を読取確認しました。現在の入力は保持しています。');}
 else if(pending.action==='resolve'){if(fp(window.WorldBloomCreationConflict.content(pending.kind,r))===fp(pending.content)){closeConflictDialog();if(pending.kind==='native')loadNative(r);else await loadNew(r);message('選択した内容の保存を読取確認しました。再送していません。');S.unknown=S.conflict=false;}else{closeConflictDialog();S.conflict=true;S.unknown=false;S.pending=null;message('現在の保存内容が選択した内容と異なります。前の送信の完了は断定せず、入力を保持しています。最新内容を比較して改めて選択してください。');}}
 else if(pending.action==='save'){S.conflict=true;S.unknown=false;S.pending=null;message('現在の保存内容が送信した内容と異なります。送信の完了は断定せず、入力を保持しています。保存内容を比較してください。');}
 else if(pending.action==='import'){loadNative(r);S.unknown=S.conflict=false;}
 else if(pending.action==='publish'&&(r.published_world||r.state==='adopted')){S.published=r.published_world||r.world_id;S.unknown=S.conflict=false;renderSummary();message('新しい世界の保存済み記録を確認しました。再保存していません。');}
 else if(pending.action==='generate'&&r.draft){loadGenerated(r);S.unknown=S.conflict=false;message('保存されたAIの別案を読み取りました。元の手入力下書きは残っています。');}
 else if(pending.action==='stage'){S.proposal=r;S.unknown=S.conflict=false;message('下案の保存を確認しました。検証はまだ実行していません。');}
 else if(pending.action==='check'&&pending.kind==='new'&&r.state==='review'&&fp(r.draft)===fp(pending.content)){S.proposal=r;S.checked={signature:semantic(),message:'保存済みの検証結果を読取確認しました。内容を確認してください。'};S.unknown=S.conflict=false;renderSummary();}
 else message('完了結果をまだ確定できません。入力を保持し、自動再送はしません。');
 }catch(e){message(e.message+' 入力を保持し、自動再送はしません。');}finally{if(!S.unknown&&!S.conflict){$('error').textContent='';S.pending=null;}S.busy=false;controls();}}
function closeConflictDialog(){if(!conflictDialog)return;const dialog=conflictDialog;conflictDialog=null;dialog.close();dialog.remove();controls();}
async function compareDraft(){
 if(S.busy||!S.conflict||!S.active||!S.id||conflictDialog)return;
 const C=window.WorldBloomCreationConflict,held=copy(content());
 const session=new C.Session({kind:S.kind,id:S.id,local:held,api,uuid});
 S.busy=true;controls();
 try{await session.read();}catch(error){message(error.message+' 現在の入力を保持しています。比較の読取は再試行できます。');return;}finally{S.busy=false;controls();}
 const dialog=node('dialog',undefined,'cw-conflict-dialog ui-edit-dialog');dialog.dataset.cwConflictDialog='true';conflictDialog=dialog;
 const heading=node('h2','下書きの保存内容を比較');heading.id='cw-conflict-title';dialog.setAttribute('aria-labelledby',heading.id);
 const note=hint(''),error=node('p','', 'ui-error'),diff=node('div',undefined,'cw-conflict-diff'),choices=node('fieldset'),legend=node('legend','この下書きに残す内容');
 choices.append(legend);let selected=null,working=false,loaded=true;
 const radios={};
 for(const [value,label] of [['current','現在の入力を残す'],['saved','保存済みの内容を残す']]){const box=node('label',undefined,'cw-conflict-choice'),radio=node('input');radio.type='radio';radio.name='conflict-side';radio.value=value;radio.addEventListener('change',()=>{selected=value;sync();});box.append(radio,node('span',label));choices.append(box);radios[value]=radio;}
 const actions=node('div',undefined,'cw-actions'),cancel=button('取消して現在の入力を保持',()=>{if(!working)closeConflictDialog();}),refreshButton=button('保存済みの内容を読み直す',refresh),recoverButton=button('送信した処理の結果を確認',async()=>{if(working)return;working=true;sync();try{await recover();}finally{working=false;if(conflictDialog===dialog)sync();}}),commitButton=button('選択した内容をこの下書きに保存',commit,'confirm');
 actions.append(cancel,refreshButton,recoverButton,commitButton);dialog.append(heading,note,error,diff,choices,actions);root.append(dialog);
 const printable=value=>value===undefined?'未設定':typeof value==='string'?value:JSON.stringify(value,null,2);
 const view=value=>C.display(session.kind,value,S.native?.model,window.WorldBloomDraftForms?.effective);
 function show(){
  diff.replaceChildren();const rows=C.differences(view(session.local),view(C.content(session.kind,session.latest)));
  note.textContent=session.readOnly?'別の画面で世界を作成済みのため、保存済みの記録は閲覧専用です。「保存済み」を選ぶと記録の表示へ切り替えます。取消では現在の入力を保持します。':'現在の入力は確定するまで変更しません。どちらを選んでも同じ下書きへ保存し、別の下書きは作りません。保存途中の他画面の更新があれば、上書きせず再比較します。';
  if(!rows.length)diff.append(hint('内容は同じです。残す内容を選び、保存済みの版を確認して確定できます。'));
  const names={world:'世界の設定',name:'世界の名前',overview:'概要',draft_text:'下案の設定',time:'時間設定',workflow:'再開位置・相談内容',edits:'設定の変更',source_digest:'保全原本の識別情報',days:'期間（日）',slots:'時間帯',step:'再開する工程',category:'編集項目',selected:'選択項目',consultation:'AI相談の入力',description:'説明',values:'設定値',actors:'実行できる人物'};
  for(const row of rows){const box=node('section',undefined,'cw-conflict-row'),parts=row.path.split('.');box.append(node('h3',(names[parts[0]]||parts[0])+(parts.length>1?' / '+parts.slice(1).map(key=>names[key]||key).join(' / '):'')));const columns=node('div',undefined,'cw-conflict-columns');for(const [title,value]of [['現在の入力',row.current],['保存済みの内容',row.saved]]){const col=node('div');const pre=node('pre',printable(value));pre.tabIndex=0;pre.setAttribute('aria-label',title+'：'+row.path);col.append(node('h4',title),pre);columns.append(col);}box.append(columns);diff.append(box);}
  const raw=node('details');raw.append(node('summary','すべての入力データを確認'));const rawColumns=node('div',undefined,'cw-conflict-columns');for(const [title,value]of [['現在の入力',session.local],['保存済みの内容',C.content(session.kind,session.latest)]]){const col=node('div'),pre=node('pre',JSON.stringify(value,null,2));pre.tabIndex=0;col.append(node('h4',title),pre);rawColumns.append(col);}raw.append(rawColumns);diff.append(raw);
  sync();
 }
 function sync(){
  for(const [side,radio]of Object.entries(radios))radio.disabled=working||!loaded||S.unknown||(session.readOnly&&side==='current');
  cancel.disabled=working;refreshButton.disabled=working||S.unknown;recoverButton.hidden=!S.unknown;recoverButton.disabled=working||!S.pending;commitButton.disabled=working||!loaded||!selected||S.unknown;
  commitButton.textContent=working?'処理中…':session.readOnly?'選択した作成済み記録を表示':'選択した内容をこの下書きに保存';
 }
 async function refresh(){
  if(working||S.unknown)return;working=true;loaded=false;selected=null;for(const radio of Object.values(radios))radio.checked=false;error.textContent='';sync();
  try{await session.read();loaded=true;show();}catch(e){error.textContent=e.message+' 現在の入力を保持しています。';}finally{working=false;sync();}
 }
 async function commit(){
  if(working||!loaded||!selected||S.unknown)return;working=true;S.busy=true;error.textContent='';sync();controls();
  try{
   const result=await session.commit(selected,pending=>{S.pending=pending;});
   closeConflictDialog();if(session.kind==='native')loadNative(result);else await loadNew(result);S.pending=null;S.unknown=S.conflict=false;message(session.readOnly?'選択した作成済み記録を閲覧しています。書込みはしていません。':'比較して選択した内容を同じ下書きに保存しました。別の下書きは作っていません。');
  }catch(e){
   S.conflict=true;S.unknown=!e.known;
   if(e.known){S.pending=null;loaded=false;selected=null;for(const radio of Object.values(radios))radio.checked=false;error.textContent=e.message+' 保存済みの内容を読み直して、もう一度比較してください。';}
   else error.textContent='送信結果が不明です。現在の入力を保持しています。再送せず「送信した処理の結果を確認」を押してください。';
  }finally{S.busy=false;working=false;controls();if(conflictDialog===dialog)sync();}
 }
 dialog.addEventListener('cancel',event=>{event.preventDefault();if(!working)closeConflictDialog();});show();dialog.showModal();
}

async function propose(){if(!readyToWrite()||!$('ai-target').value||!$('ai-instruction').value.trim()||S.aiId)return;if(dirty()||!S.id){message('先に下書きを保存してください。手修正を保持した版へAI候補を作ります。');return;}const target=JSON.parse($('ai-target').value),instruction=$('ai-instruction').value;await run('ai',async()=>{
 if(S.kind==='new')await ensureProposal();const id=uuid(),epoch=++S.epoch;S.aiId=id;S.ai=null;S.aiCancelled=false;S.aiBase=semantic();carry();controls();$('ai-status').textContent='提案を作っています。現在の下書きは変更しません。';S.pending={action:'ai',kind:S.kind,id:S.kind==='native'?S.id:S.proposal.request_id};const r=S.kind==='native'?await api('/api/native-world-drafts/'+S.id+'/revise',{revision:S.native.revision,request_id:id,target,instruction}):await api('/api/world-drafts/'+S.proposal.request_id+'/revise',{revision:S.proposal.edit_revision||S.proposal.revision,request_id:id,draft:S.draft,target,instruction});
 if(epoch!==S.epoch||S.aiCancelled){$('ai-status').textContent='返ってきた提案は取り消し済みのため適用しません。';return;}S.ai=r;S.aiBase=semantic();$('ai-status').textContent=r.state==='ready'?'未反映の提案があります。差分を確認してから適用してください。':'提案を確定できませんでした。状態を確認するか、取り消してください。';renderAI();
 });}
async function aiCheck(){if(S.busy||!S.aiId)return;S.busy=true;controls();try{
 const base=S.kind==='native'?'/api/native-world-drafts/'+S.id:'/api/world-drafts/'+S.proposal.request_id;
 const r=await api(base+'/revisions/'+S.aiId);
 if(r.state==='applied'){
  if(S.pending?.action==='ai-apply'&&S.aiBase===semantic()){
   const current=await api(base);
   if(S.kind==='native'){const w=workflow();S.native=current;S.buffer={...copy(current.buffer),workflow:w};S.saved=semantic();}
   else{S.proposal=current;S.draft=copy(current.draft);S.raw=JSON.stringify(S.draft,null,2);}
   S.ai=S.aiId=null;S.pending=null;S.unknown=S.conflict=false;S.checked=null;carry();renderFields();renderAI();$('ai-status').textContent='保存済みの適用結果を確認しました。再適用していません。';
  }else $('ai-status').textContent='適用済みです。現在の手修正は保持しています。保存した下書きを確認してください。';
 }else if(S.aiCancelled){
  if(r.state==='cancelled'||S.kind==='new'){S.ai=S.aiId=null;S.pending=null;S.unknown=S.conflict=false;carry();$('ai-status').textContent='提案を取り消した状態を確認しました。';}
  else $('ai-status').textContent='取消結果はまだ確定できません。入力を保持しています。';
 }else{
  S.ai=r;if(S.pending?.action==='ai'){S.unknown=S.conflict=false;S.pending=null;}renderAI();$('ai-status').textContent='保存済みの提案状態：'+r.state+'。AIを再度呼んでいません。';
 }
 }catch(e){$('ai-status').textContent=e.message;}finally{S.busy=false;controls();}}
async function cancelAI(){if(!S.aiId||S.aiCancelled)return;S.aiCancelled=true;++S.epoch;const id=S.aiId;S.ai=null;renderAI();$('ai-status').textContent='提案を取り消しました。実行中のAI問い合わせ自体は完了待ちになる場合があります。';if(S.kind==='native'){try{await api('/api/native-world-drafts/'+S.id+'/cancel-revision',{request_id:id});}catch(e){$('ai-status').textContent=e.message+' 取消結果は不明です。再送せず状態を確認してください。';S.unknown=true;}}if(!S.busy){S.aiId=null;carry();}controls();}
async function applyAI(){if(!readyToWrite()||!S.ai||S.ai.state!=='ready'||S.aiCancelled||S.aiBase!==semantic())return;await run('apply-ai',async()=>{S.pending={action:'ai-apply',kind:S.kind,id:S.kind==='native'?S.id:S.proposal.request_id};if(S.kind==='native'){const r=await api('/api/native-world-drafts/'+S.id+'/apply-revision',{revision:S.native.revision,request_id:S.aiId});S.native=r;S.buffer=copy(r.buffer);S.saved=semantic();}else{S.proposal=await api('/api/world-drafts/'+S.proposal.request_id+'/apply-revision',{revision:S.proposal.edit_revision||S.proposal.revision,request_id:S.aiId,draft:S.draft});S.draft=copy(S.proposal.draft);S.raw=JSON.stringify(S.draft,null,2);}S.ai=null;S.aiId=null;changed();carry();renderFields();renderAI();$('ai-status').textContent='提案を適用しました。新しい内容は下書きとして保存してください。';message('提案を下書きに適用しました。世界への採用は行っていません。');});}
function loadGenerated(r,screen='workflow'){S.screen=screen;S.active=true;S.kind='new';S.proposal=r;S.draft=copy(r.draft);S.raw=JSON.stringify(S.draft,null,2);S.id=null;S.revision=null;S.native=null;S.buffer=null;S.originalContent={...(window.WorldBloomCreationGeneration.metadata(r)?{generation:window.WorldBloomCreationGeneration.metadata(r)}:{})};S.authoring=copy(r.authoring||null);S.prep.name=r.name;S.prep.overview=r.overview;S.prep.genre=r.authoring?.outline?.genre||'';S.prep.storyType=r.authoring?.type||'';S.saved=null;S.checked=null;S.published=null;$('consent').checked=false;S.step=2;carry();render();}
async function generate(){if(S.kind==='native'||!guard()||!S.prep.name.trim()||!S.prep.overview.trim()){message('AIへの相談には世界の名前と概要を入力してください。AIなしでは未完成のまま次へ進めます。');return;}if(!window.confirm('現在の手入力を下書き保存して残し、AIで別の下案を作りますか？ AIを呼び出します。'))return;if(!await saveDraft())return;await run('generate',async()=>{const id=uuid();S.pending={action:'generate',kind:'new',id};const r=await api('/api/world-drafts',{request_id:id,name:S.prep.name,overview:S.prep.overview,...(S.draft?.settings?{settings:S.draft.settings}:{}),authoring:authoring()});if(!r.draft)throw Object.assign(Error('下案を生成できませんでした。手入力の下書きは保存したままです。'),{known:true});loadGenerated(r);message('AIの別案を表示しています。元の手入力は保存済み下書きから再開できます。');});}
function detailEditor(){if(S.kind==='native'||!S.draft||!guard())return;$('detail-editor').closest('details').open=false;
 const dialog=node('dialog',undefined,'cw-detail-dialog ui-edit-dialog'),title=node('h2','目標の達成条件・関係・導入文');title.id='cw-detail-title';dialog.setAttribute('aria-labelledby',title.id);
 const host=node('div'),actions=node('div',undefined,'cw-actions');let candidate=copy(S.draft),pending=false;
 dialog.append(title,hint('ここでの変更は「下書きへ反映」を押すまで保留します。取消で開く前の入力に戻ります。持ち物の所持者は持ち物カテゴリで編集します。'),host,actions);root.append(dialog);
 const editor=new window.WorldBloomDraftGoals.Editor(host,document,d=>{candidate=d;pending=true;});editor.itemsEditor=()=>{};
 const originalRender=editor.render.bind(editor);editor.render=d=>{originalRender(d);for(const p of host.querySelectorAll('.ui-hint'))p.textContent=p.textContent.replace('修正後は下の「下案を確認する」を押してください。','最後に「下書きへ反映」を押し、下書きを保存して確認画面で検証してください。');};
 const originalDialog=editor.textDialog.bind(editor);editor.textDialog=(title,fields,apply,opener,options={})=>originalDialog(title,fields,apply,opener,{...options,note:options.note||'保存するとこの詳細フォーム内の候補に反映します。最後に「下書きへ反映」を押すまで、現在の下書きは変更しません。'});editor.render(candidate);
 const close=()=>{if(pending&&!window.confirm('この詳細フォームの変更を取り消しますか？'))return;dialog.close();};
 actions.append(button('キャンセル',close),button('下書きへ反映',()=>{S.draft=copy(candidate);S.raw=JSON.stringify(S.draft,null,2);changed();dialog.close();},'confirm'));
 dialog.addEventListener('cancel',e=>{e.preventDefault();close();});dialog.addEventListener('close',()=>{dialog.remove();renderFields();renderAI();controls();});dialog.showModal();
}
refinement=window.WorldBloomCreationRefinement?.mount(root,{
 api,uuid,lock:value=>{S.refinementGate=value;controls();},
 snapshot:()=>({kind:S.kind,active:S.active&&S.screen==='workflow',step:S.step,published:S.published,blocked:S.busy||S.unknown||S.conflict||S.generationGate||!!S.aiId,identity:S.id||'held-new',signature:semantic(),people:(S.draft?.people||[]).map(p=>p.name).filter(Boolean),person:S.draft?.people?.[S.selected]?.name}),
 prepare:async()=>{
  if(S.kind!=='new'||!S.draft||!readyToWrite())return null;
  if(!await saveDraft())return null;
  await ensureProposal();
  if(fp(S.proposal.draft)!==fp(S.draft))S.proposal=await api('/api/world-drafts/'+S.proposal.request_id+'/save-refinement-input',{revision:S.proposal.edit_revision||S.proposal.revision,draft:S.draft});
  if(!await saveDraft())return null;
  return {token:S.proposal.request_id,revision:S.proposal.edit_revision||S.proposal.revision};
 },
 install:async record=>{
  if(!record?.draft)throw Error('採用結果の下書きを取得できません。状態を確認してください。');
  S.proposal=record;S.draft=copy(record.draft);S.raw=JSON.stringify(S.draft,null,2);S.checked=null;S.published=null;$('consent').checked=false;carry();renderFields();renderAI();
  if(!await saveDraft())throw Error('候補は採用済みですが、編集中の下書きの保存確認が必要です。再採用せず保存結果を確認してください。');
  message('提案を下書きに採用・保存しました。世界確定・GA・文章生成は行っていません。');
 }
});
initialGeneration=window.WorldBloomCreationGeneration.mount(root,{
 api,uuid,save:()=>{ensureDraft();return saveDraft();},message,
 snapshot:()=>({kind:S.kind,id:S.id,revision:S.revision,content:content()}),
 lock:value=>{S.generationGate=value;controls();},
 track:id=>{const u=new URL(location.href);if(id)u.searchParams.set('cw_generation',id);else u.searchParams.delete('cw_generation');history.replaceState(history.state,'',u.pathname+u.search+u.hash);},
 manual:()=>navigate(2),
 apply:(c,result)=>{S.originalContent={...S.originalContent,...(c.generation?{generation:copy(c.generation)}:{})};S.proposal=result;S.draft=JSON.parse(c.draft_text);S.raw=c.draft_text;S.checked=null;S.published=null;$('consent').checked=false;S.screen='workflow';S.step=2;S.category='people';S.selected=0;carry();render();message((result.mode==='mock'?'固定応答の試験用設定を入力しました。実AIは呼んでいません。':'AIが設定のたたき台を入力しました。')+'内容を確認・修正し、下書きを保存してください。まだ世界を確定していません。');}
});
function prepareNext(){if(!guard())return;if(S.kind==='native')navigate(2);else initialGeneration.open();}
for(const b of root.querySelectorAll('[data-cw-enter]'))b.addEventListener('click',()=>b.dataset.cwEnter==='new'?beginNew():navigateScreen(b.dataset.cwEnter));
$('home').addEventListener('click',e=>{e.preventDefault();navigateScreen('entry');});
for(const b of root.querySelectorAll('[data-cw-step]'))b.addEventListener('click',()=>navigate(Number(b.dataset.cwStep)));
$('prepare').addEventListener('submit',e=>{e.preventDefault();prepareNext();});$('prepare').addEventListener('input',e=>{if(S.kind==='native')return;S.prep[e.target.name]=e.target.value;syncIdentity();changed();});$('prepare').addEventListener('change',e=>{if(S.kind==='native')return;S.prep[e.target.name]=e.target.value;syncIdentity();changed();});
$('next').addEventListener('click',()=>{if(S.screen==='entry'){if(S.active)navigate(S.step);return;}if(S.screen!=='workflow')return;if(S.published&&S.step===3){location.href='/worlds/'+encodeURIComponent(S.published);return;}if(S.step===1)prepareNext();else if(S.step<3)navigate(S.step+1);else publish();});$('continue').addEventListener('click',()=>{if(S.active)navigate(S.step);});$('save').addEventListener('click',saveDraft);$('check').addEventListener('click',check);$('consent').addEventListener('change',controls);$('save-check').addEventListener('click',recover);$('compare').addEventListener('click',compareDraft);
for(const input of [$('source'),$('zip')])input.addEventListener('change',controls);
$('refresh').addEventListener('click',()=>refresh());
$('import').addEventListener('click',()=>{if($('source').value)importSource({world_id:$('source').value});});$('import-zip').addEventListener('click',async()=>{const file=$('zip').files?.[0];if(!file)return;if(file.size>10*1024*1024){$('error').textContent='ZIPは10MB以内にしてください';return;}const bytes=new Uint8Array(await file.arrayBuffer());let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));await importSource({zip_base64:btoa(text)});});
$('ai-target').addEventListener('change',()=>{aiTargets[S.category]=$('ai-target').value;controls();});$('ai-instruction').addEventListener('input',controls);$('ai-propose').addEventListener('click',propose);$('ai-check').addEventListener('click',aiCheck);$('ai-cancel').addEventListener('click',cancelAI);$('ai-apply').addEventListener('click',applyAI);$('generate').addEventListener('click',generate);$('detail-editor').addEventListener('click',detailEditor);
$('json').addEventListener('input',()=>{if(S.kind==='native')return;S.raw=$('json').value;try{S.draft=JSON.parse(S.raw);}catch(_){S.draft=null;}S.checked=null;$('consent').checked=false;controls();});$('json-apply').addEventListener('click',()=>{if(!S.draft){$('error').textContent='JSONの書式を確認してください。入力は下書き保存できます。';return;}renderFields();renderAI();message('JSONの内容を画面へ反映しました。まだ検証・世界採用はしていません。');});
window.addEventListener('popstate',e=>{const target=e.state?.cwIndex??0;if(bouncing){bouncing=false;return;}if(!guard()){const delta=historyIndex-target;if(delta){bouncing=true;history.go(delta);}return;}historyIndex=target;S.screen=e.state?.cwScreen==='import'?'world-import':e.state?.cwScreen||'entry';S.step=e.state?.cwStep||1;if(S.screen==='workflow'&&S.step>1)ensureDraft();carry();render();if(S.screen==='resume')refresh().catch(()=>message('一覧を取得できません。「一覧を更新」で確認してください。'));});
window.addEventListener('beforeunload',e=>{if(root.querySelector('dialog[open]')||S.busy||S.unknown||S.conflict||dirty()||window.WorldBloomDraftForms?.hasPending()){e.preventDefault();e.returnValue='';}});
const initial=new URL(location.href),initialScreen=M.initialScreen(initial.searchParams);S.screen=initialScreen;S.active=initialScreen==='workflow';S.step=/^[123]$/.test(initial.searchParams.get('step')||'')?Number(initial.searchParams.get('step')):1;if(S.active&&S.step>1)ensureDraft();history.replaceState({...history.state,cwIndex:0,cwStep:S.step,cwScreen:S.screen},'',location.href);render();refresh().catch(()=>message('一覧を取得できません。「一覧を更新」で確認してください。'));
const nativeId=initial.searchParams.get('native_draft'),workspaceId=initial.searchParams.get('workspace'),draftId=initial.searchParams.get('draft');let initialLoad=Promise.resolve();if(!nativeId&&!workspaceId&&!draftId)carry();if(nativeId&&/^[a-f0-9]{32}$/.test(nativeId))initialLoad=run('open',async()=>loadNative(await api('/api/native-world-drafts/'+nativeId),initialScreen));else if(workspaceId&&/^[a-f0-9]{32}$/.test(workspaceId))initialLoad=run('open',async()=>loadNew(await api('/api/world-draft-workspaces/'+workspaceId),initialScreen));else if(draftId&&/^[a-f0-9]{32}$/.test(draftId))initialLoad=run('open',async()=>{const r=await api('/api/world-drafts/'+draftId);if(!r.draft)throw Object.assign(Error('下案の内容がありません。元の下書きから再開してください。'),{known:true});loadGenerated(r,initialScreen);message('保存済みの下案を開きました。AI・検証・採用は自動実行していません。');});initialLoad.then(()=>initialGeneration.resume(initial.searchParams.get('cw_generation')));
})();
