/* Scoped draft forms. Text is never interpolated as markup. No model call. */
((global)=>{
'use strict';
const copy=v=>structuredClone(v);
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const button=(text,fn)=>{const b=node('button',text,'ui-button');b.type='button';b.addEventListener('click',fn);return b;};
function field(host,label,value,change,type='text'){
 const wrap=node('label',undefined,'ui-field'),input=node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.value=value??'';if(type==='textarea')input.rows=3;
 input.setAttribute('aria-label',label);wrap.append(node('span',label),input);host.append(wrap);input.addEventListener('input',()=>change(input.value));return input;
}
function select(host,label,value,choices,change){
 const wrap=node('label',undefined,'ui-field'),input=node('select');input.setAttribute('aria-label',label);
 for(const choice of choices){const [v,text]=Array.isArray(choice)?choice:[choice,choice],o=node('option',text);o.value=v;input.append(o)}
 if(value!==undefined&&value!==null&&!choices.some(c=>(Array.isArray(c)?c[0]:c)===value)){const o=node('option',value+'（現在値を保持）');o.value=value;input.append(o)}
 input.value=value??'';wrap.append(node('span',label),input);host.append(wrap);input.addEventListener('change',()=>change(input.value));return input;
}
function checkbox(host,label,checked,change){const wrap=node('label',undefined,'ui-field'),c=node('input');c.type='checkbox';c.checked=!!checked;wrap.append(c,node('span',label));host.append(wrap);c.addEventListener('change',()=>change(c.checked));return c;}
function choices(host,label,values,selected,change){
 const box=node('fieldset');box.append(node('legend',label));box.className='native-picker';
 for(const value of values)checkbox(box,value,selected.includes(value),()=>change([...box.querySelectorAll('input:checked')].map(e=>e.value))).value=value;
 host.append(box);
}
function effective(base,edits){
 const m=copy(base);
 for(const op of edits){const v=copy(op.values);
  if(op.kind==='time'){Object.assign(m.time,v);if(typeof m.time.slots==='string')m.time.slots=m.time.slots.split('\n');}
  if(op.kind==='event'){const index=op.target===null?m.scheduled_events.length:op.target;m.scheduled_events[index]={...(m.scheduled_events[index]||{}),...v};}
  if(op.kind==='action')m.constraints[op.target]={...(m.constraints[op.target]||{}),...v};
  if(op.kind==='goal'){const p=m.people.find(p=>p.name===op.target);if(p)p.goal={...p.goal,...v};}
  if(op.kind==='commitments'){const p=m.people.find(p=>p.name===op.target);if(p)p.commitments=v.commitments;for(const c of v.additions||[])if(!m.conditions.some(x=>x.id===c.id))m.conditions.push(c);}
 }
 return m;
}
const labels={move:'移動',observe:'観察',investigate:'調べる',rest:'休息',give_item:'品物を渡す',persuade:'説得',fight:'戦う',share_knowledge:'情報を伝える',labor:'働く',buy:'購入',plant:'伏線を置く',payoff:'伏線を回収'};
class Editor{
 constructor(host,{model,edits=[],onChange,native=false}){this.host=host;this.base=copy(model);this.edits=copy(edits);this.changed=onChange;this.native=native;this.section='time';this.actor=model.people[0]?.name;this.event=0;this.action='verb:move';}
 model(){return effective(this.base,this.edits)}
 put(kind,target,values){const op=this.edits.find(o=>o.kind===kind&&o.target===target);if(op)Object.assign(op.values,copy(values));else this.edits.push({kind,target,values:copy(values)});this.changed?.(copy(this.edits));}
 render(section=this.section){this.section=section;this.host.replaceChildren();const m=this.model();
  if(section==='time')this.time(m);else if(section==='events')this.events(m);else if(section==='actions')this.actions(m);else this.people(m);
 }
 time(m){
  this.host.append(node('h3','期間と時間帯'),node('p','未対応の暦や日時の詳細は保持します。時間帯を変えるときは予定・行動制限・期限の参照も検査します。','ui-hint'));
  if(!m.ordinary){this.host.append(node('p','この実行方式の時間変更は未対応です。保存済みの設定を保持します。','ui-notice'));return;}
  const state={days:String(m.time.days??''),slots:Array.isArray(m.time.slots)?m.time.slots.join('\n'):m.time.slots??''};
  field(this.host,'期間（日）',state.days,v=>{state.days=v;this.put('time',null,state)},'number');field(this.host,'時間帯（1行に一つ）',state.slots,v=>{state.slots=v;this.put('time',null,state)},'textarea');
  if(m.time.date_calendar)this.host.append(node('p','実在暦・休日設定を保持しています。期間と矛盾した場合は検証で停止します。暦の詳細変更は既存の時間設定画面で扱います。','ui-notice'));
 }
 events(m){
  this.host.append(node('h3','予定された出来事'),node('p','既存の未対応項目は保持します。対象の人物と、起きる変化を指定してください。','ui-hint'));
  if(!m.ordinary){this.host.append(node('p','この実行方式の予定編集は未対応です。','ui-notice'));return;}
  select(this.host,'予定を選ぶ',String(Math.min(this.event,Math.max(0,m.scheduled_events.length-1))),m.scheduled_events.map((e,i)=>[String(i),e.label||e.id||'未完成の予定']),v=>{this.event=Number(v);this.render()});
  this.host.append(button('予定を追加',()=>{const index=m.scheduled_events.length;this.put('event',index,{id:'event_'+crypto.randomUUID().slice(0,8),label:'',day:'',slot:'',targets:[]});this.event=index;this.render()}));
  const e=m.scheduled_events[this.event];if(!e)return;const target=this.event,update=(k,v)=>this.put('event',target,{[k]:v});
  field(this.host,'予定ID',e.id,v=>update('id',v));field(this.host,'予定の名前',e.label,v=>update('label',v));field(this.host,'実施日（第何日）',e.day,v=>update('day',v),'number');
  select(this.host,'実施する時間帯',e.slot||'',[['','日単位（時間帯を指定しない）'],...(Array.isArray(m.time.slots)?m.time.slots:[])],v=>update('slot',v));
  choices(this.host,'対象の人物',m.people.map(p=>p.name),e.targets||[],v=>update('targets',v));
  const details=node('details');details.append(node('summary','起きる変化・指定行動'));this.host.append(details);
  field(details,'ストレスの増減',e.stress_delta??'',v=>update('stress_delta',v),'number');
  const grant=copy(e.grants_item||{name:'',count:1});select(details,'与える品物',grant.name||'',[['','付与なし'],...m.items],v=>{grant.name=v;update('grants_item',v?grant:null)});field(details,'与える個数',grant.count,v=>{grant.count=v;if(grant.name)update('grants_item',grant)},'number');
  select(details,'伝わる情報',e.grants_fact||'',[['','情報の付与なし'],...m.facts],v=>update('grants_fact',v));select(details,'直接移動させる場所',e.move_to||'',[['','直接移動なし'],...m.places],v=>update('move_to',v));
  const force=copy(e.force_action||{verb:'',args:[]});select(details,'指定して実行する行動',force.verb,[['','指定しない（自由な選択を保持）'],...Object.keys(m.force_verbs).map(v=>[v,(m.action_labels?.['verb:'+v]||labels[v]||v)+'（'+v+'）'])],v=>{force.verb=v;update('force_action',v?force:null)});
  field(details,'指定行動の引数（1行に一つ・登録済みの名前）',(force.args||[]).join('\n'),v=>{force.args=v?v.split('\n'):[];if(force.verb)update('force_action',force)},'textarea');
  details.append(node('p','指定行動には時間帯が必要です。直接移動との併用、人物の許可・所持品・参照を検証します。','ui-hint'));
 }
 actions(m){
  this.host.append(node('h3','行動の人物・時間制限'),node('p','このフォームは人物と実行可能な時間帯だけを変更します。使用ON/OFFや、材料・知識などの条件は保持します。','ui-hint'));
  if(!m.ordinary||!m.action_policy){this.host.append(node('p','この世界の行動制限編集は未対応です。設定を保持しています。','ui-notice'));return;}
  select(this.host,'行動を選ぶ',this.action,m.actions.map(ref=>[ref,(m.action_labels?.[ref]||labels[ref.replace('verb:','')]||ref)+' ／ '+(m.enabled.includes(ref)?'使用中':'使用しない（保持）')]),v=>{this.action=v;this.render()});
  if(this.action==='verb:rest'){const c=checkbox(this.host,'必須の休息（人物・時間の制限なし）',true,()=>{});c.disabled=true;c.dataset.nativeLocked='true';return;}
  const rule=copy(m.constraints[this.action]||{actors:null,slots:null}),ref=this.action;
  const peopleBox=node('div'),timeBox=node('div');
  checkbox(this.host,'人物を個別に指定する',rule.actors!==null,checked=>{rule.actors=checked?[]:null;this.put('action',ref,rule);this.render()});
  if(rule.actors===null)this.host.append(node('p','既存の人物の能力・行動許可を使います。全員への一括許可ではありません。','ui-hint'));
  else{field(peopleBox,'人物を検索','',value=>{for(const label of peopleBox.querySelectorAll('fieldset label'))label.hidden=!label.textContent.includes(value)},'search');choices(peopleBox,'実行を許可する人物',m.people.map(p=>p.name),rule.actors,v=>{rule.actors=v;this.put('action',ref,rule)});this.host.append(peopleBox);}
  checkbox(this.host,'実行時間を制限する',rule.slots!==null,checked=>{rule.slots=checked?[]:null;this.put('action',ref,rule);this.render()});
  if(rule.slots!==null){timeBox.append(node('p','チェックした時間帯に実行できます。チェックしない時間帯は実行できません。','ui-hint'));choices(timeBox,'実行できる時間帯',Array.isArray(m.time.slots)?m.time.slots:[],rule.slots,v=>{rule.slots=v;this.put('action',ref,rule)});this.host.append(timeBox);}
 }
 people(m){
  this.host.append(node('h3','人物の目的・優先事項'));
  select(this.host,'人物を選ぶ',this.actor,m.people.map(p=>p.name),v=>{this.actor=v;this.render()});const p=m.people.find(p=>p.name===this.actor);if(!p)return;
  if(this.native){const goal={description:p.goal.description||'',target:p.goal.target||'',deliver_to:p.goal.deliver_to||''};
   field(this.host,'目的の説明（自由記述）',goal.description,v=>{goal.description=v;this.put('goal',p.name,goal)},'textarea');
   select(this.host,'獲得したい品物',goal.target,[['','目標の品物なし'],...m.items],v=>{goal.target=v;this.put('goal',p.name,goal)});select(this.host,'届け先',goal.deliver_to,[['','届け先なし'],...m.places],v=>{goal.deliver_to=v;this.put('goal',p.name,goal)});
  }else this.host.append(node('p','目的と達成条件は、この人物の既存の目標フォームから編集できます。ここでは優先事項を設定します。','ui-hint'));
  this.host.append(node('p','文章の目的と、優先する状態・優先度・必須指定は別です。自動推定しません。','ui-hint'));
  const prior=this.edits.find(o=>o.kind==='commitments'&&o.target===p.name),state={commitments:copy(p.commitments||[]),additions:copy(prior?.values.additions||[])};
  const update=()=>this.put('commitments',p.name,state);
  state.commitments.forEach((row,index)=>{const box=node('fieldset');box.append(node('legend','優先事項 '+(index+1)));select(box,'条件 '+(index+1),row.condition,[['','条件を選択'],...m.conditions.map(c=>[c.id,c.label])],v=>{row.condition=v;update()});field(box,'優先度 '+(index+1)+'（1〜10）',row.priority,v=>{row.priority=v;update()},'number');checkbox(box,'必須 '+(index+1),row.required,v=>{row.required=v;update()});box.append(button('この優先事項を外す',()=>{state.commitments.splice(index,1);update();this.render()}));this.host.append(box)});
  this.host.append(button('登録済みの条件から優先事項を追加',()=>{state.commitments.push({condition:'',priority:'1',required:false});update();this.render()}));
  const add=node('details');add.append(node('summary','新しい条件を定義して追加'));this.host.append(add);
  const c={id:'draft_condition_'+crypto.randomUUID().slice(0,8),label:'',mode:'achieve',visibility:'observed'},test={kind:'zone',actor:p.name,value:'',minimum:'1'};
  field(add,'新しい条件の名前',c.label,v=>c.label=v);select(add,'条件の評価',c.mode,[['achieve','達成したい状態'],['maintain','保ちたい状態']],v=>c.mode=v);select(add,'条件の認識',c.visibility,[['observed','人物が認識している情報'],['public','世界の状態を直接評価']],v=>c.visibility=v);
  select(add,'条件の種類',test.kind,[['zone','指定した場所にいる'],['inventory','指定した品物を持つ'],['knowledge','指定した情報を知る']],v=>{test.kind=v;ref.replaceChildren();select(ref,'場所・品物・情報',test.value,[['','選択してください'],...(v==='zone'?m.places:v==='inventory'?m.items:m.facts)],value=>test.value=value)});
  const ref=node('div');select(ref,'場所・品物・情報',test.value,[['','選択してください'],...m.places],v=>test.value=v);add.append(ref);field(add,'必要な個数（品物の条件）',test.minimum,v=>test.minimum=v,'number');
  add.append(button('この条件を優先事項へ追加',()=>{if(!c.label.trim()||!test.value){add.append(node('p','条件の名前と参照先を指定してください。','ui-error'));return;}const definition={...c,test:test.kind==='zone'?{kind:'zone',actor:p.name,equals:test.value}:test.kind==='inventory'?{kind:'inventory',actor:p.name,item:test.value,minimum:Number(test.minimum)}:{kind:'knowledge',fact:test.value,actors:[p.name],rule:'all'}};state.additions.push(definition);state.commitments.push({condition:c.id,priority:'1',required:false});update();this.render()}));
 }
}
const activeStates=new Set();
const pendingStates=()=>[...activeStates].filter(s=>s.pending&&s.host.isConnected);
global.WorldBloomDraftForms={Editor,effective,
 mountTime(host,{draft,onApply,getDraft}){
  const M=global.WorldBloomCreationModel;
  if(draft.settings!==undefined&&(!draft.settings||typeof draft.settings!=='object'||Array.isArray(draft.settings))||draft.settings&&Object.hasOwn(draft.settings,'time')&&(!draft.settings.time||typeof draft.settings.time!=='object'||Array.isArray(draft.settings.time))){host.append(node('p','保存済みの時間設定の形式を確認してください。既定値で置き換えず保持しています。詳細編集の下案JSONで修復できます。','ui-error'));return;}
  const t=draft.settings?.time===undefined?M.defaultTiming():draft.settings.time,state={pending:false,host};activeStates.add(state);
  const message=node('p','期間・時間帯と導入文は、人物や場所が未設定でも編集できます。予定・期限・行動との整合は③で確認します。','ui-hint');host.append(node('h3','期間と時間帯'),message);
  const days=field(host,'物語の期間（日）',t.days??'',()=>state.pending=true,'number');days.min='1';days.max='10000';days.step='1';
  const slots=field(host,'時間帯（1行に一つ）',Array.isArray(t.slots)?t.slots.join('\n'):typeof t.slots==='string'?t.slots:JSON.stringify(t.slots??''),()=>state.pending=true,'textarea');
  const intro=field(host,'物語の導入文',draft.initial_story??'',()=>state.pending=true,'textarea');intro.maxLength=8000;
  host.append(node('p','導入文は物語の開始を説明する文章です。文章だけから人物の知識・危険や実行ルールを自動設定しません。','ui-hint'));
  if(t.date_calendar||t.presentation||Object.keys(t).some(k=>!['days','slots'].includes(k)))host.append(node('p','保存済みの暦・表示やその他の時間設定は保持します。期間・時間帯を変えた後は③で矛盾を確認してください。','ui-notice'));
  host.append(button('フォームの変更を下書きに反映',()=>{
   if(getDraft&&JSON.stringify(getDraft())!==JSON.stringify(draft)){message.textContent='下案が変わりました。入力を保持しています。フォームを開き直して確認してください。';return;}
   try{const next=M.editTime(draft,days.value,slots.value,intro.value);state.pending=false;activeStates.delete(state);onApply(next);}catch(e){message.textContent=e.message;message.className='ui-error';}
  }),button('フォームの変更を取り消す',()=>{state.pending=false;activeStates.delete(state);host.replaceChildren();global.WorldBloomDraftForms.mountTime(host,{draft,getDraft,onApply});}));
 },
 hasPending:()=>pendingStates().length>0,
 canLeave(){if(!pendingStates().length)return true;if(!global.confirm('フォームの未反映入力を破棄して移動しますか？'))return false;for(const state of pendingStates())state.pending=false;return true;},
 async mount(host,{draft,section,onApply,getDraft}){
  const message=node('p','フォームを読み込んでいます。','ui-hint');host.append(message);const state={pending:false,host};activeStates.add(state);
  try{const res=await fetch('/api/world-draft-forms',{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify({draft})});const model=await res.json();if(!res.ok)throw Error(model.message||'フォームを読み込めません');if(!host.isConnected)return;
   message.textContent='フォームの変更を下書きに反映してから、下書き全体を保存・確認してください。AIは呼びません。';
   const box=node('div');host.append(box);let edits=[];const editor=new Editor(box,{model,onChange:v=>{edits=v;state.pending=true;}});editor.render(section);
   host.append(button('フォームの変更を下書きに反映',async()=>{if(getDraft&&JSON.stringify(getDraft())!==JSON.stringify(draft)){message.textContent='下案が変わりました。入力を保持しています。このフォームは再度開いてください。';return;}const controls=[...host.querySelectorAll('input,select,button,textarea')];controls.forEach(e=>e.disabled=true);try{const result=await fetch('/api/world-draft-forms',{method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify({draft,edits})});const next=await result.json();if(!result.ok)throw Error(next.message||'入力を確認してください');if(getDraft&&JSON.stringify(getDraft())!==JSON.stringify(draft))throw Error('確認中に下案が変わりました。上書きしません。');state.pending=false;activeStates.delete(state);onApply(next);}catch(e){message.textContent=e.message;message.className='ui-error';controls.forEach(e=>e.disabled=false);}}));
  }catch(e){message.textContent=e.message;message.className='ui-error';}
 }
};
global.addEventListener('beforeunload',e=>{if(pendingStates().length){e.preventDefault();e.returnValue='';}});
})(window);
