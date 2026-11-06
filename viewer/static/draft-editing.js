/* Item-scoped editors. AI candidates fill the same fields as human edits. */
(function(global){
 const copy=v=>JSON.parse(JSON.stringify(v)),el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};
 const names={goal:'目標と達成条件',profile:'人物の紹介・人物像・開始位置',description:'場所の説明',holder:'品物の所持者',story:'初期物語',relation:'人物間の関係',route:'経路の移動条件',roles:'主人公と相手役'};
 let modalNumber=0;
 class Editing{
  constructor(options){this.options=options;this.disabled=false;this.active=null;this.pencils=[];}
  button(label,action){const b=el('button','ui-button',label);b.type='button';b.onclick=action;return b;}
  mount(target,content){
   if(this.active)this.dispose(this.active);
   this.active=null;this.pencils=[];
   for(const block of content.querySelectorAll('[data-edit-field]')){
    const field=block.dataset.editField,spec={...target,field};if(block.dataset.editSection){spec.section=block.dataset.editSection;spec.entity='';}if(block.dataset.editEntity)spec.entity=block.dataset.editEntity;if(block.dataset.editPeer)spec.peer=block.dataset.editPeer;
    const head=el('div','field-card-head'),heading=block.querySelector(':scope>h3,:scope>h4')||el('h3','',names[field]);if(!heading.parentNode)block.prepend(heading);block.insertBefore(head,heading);head.append(heading);
    const pencil=this.button('',()=>this.open(block,head,pencil,spec));pencil.classList.add('field-pencil');pencil.dataset.fieldPencil=field;
    pencil.fieldSpec=spec;
    pencil.setAttribute('aria-label',(spec.entity?spec.entity+'の':'')+names[field]+'を編集');pencil.title=names[field]+'を編集';
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');const path=document.createElementNS(icon.namespaceURI,'path');path.setAttribute('d','M4 16.5V20h3.5L19 8.5 15.5 5 4 16.5zm17-10a1 1 0 0 0 0-1.4l-2.1-2.1a1 1 0 0 0-1.4 0L16 4.5 19.5 8 21 6.5z');icon.append(path);pencil.append(icon);head.append(pencil);this.pencils.push(pencil);
   }
   this.setDisabled(this.disabled);
  }
  setDisabled(value){
   this.disabled=value;for(const b of this.pencils)b.disabled=value||!!this.active;
   if(this.active){for(const field of this.active.dialog.querySelectorAll('button,input,textarea,select'))field.disabled=((value||this.active.pending)&&(!field.hasAttribute('data-edit-cancel')||this.active.committing))||field.dataset.fieldLocked==='true';}
  }
  canLeave(){if(!this.active)return true;if(this.active.pending)return false;if(this.active.changed&&!global.confirm('この項目の編集中の内容を破棄して移動しますか？'))return false;this.close();return true;}
  paths(d,s){
   if(s.field==='roles')return ['protagonist','antagonist'];if(s.field==='story')return ['initial_story'];
   if(s.section==='routes')return ['routes',Number(s.entity)];
   const key=s.section==='relations'?'people':s.section,index=d[key].findIndex(p=>p.name===s.entity);
   if(s.field==='relation'){const ri=d.people[index].relations.findIndex(r=>r.person===s.peer);return ['people',index,'relations',ri];}
   return [key,index];
  }
  value(d,s){const p=this.paths(d,s);if(s.field==='roles')return {protagonist:d.protagonist,antagonist:d.antagonist};if(s.field==='story')return d.initial_story;let v=d;for(const k of p)v=v[k];if(s.field==='goal')return copy(global.WorldBloomDraftGoals.goalValue(v.goal));if(s.field==='profile')return {description:v.description,personality:v.personality,entry:v.entry};if(s.field==='description'||s.field==='holder')return v[s.field];return copy(v);}
  put(d,s,v){
   if(s.field==='roles'){d.protagonist=v.protagonist;d.antagonist=v.antagonist;return;}if(s.field==='story'){d.initial_story=v;return;}
   const p=this.paths(d,s);let owner=d;for(const k of p.slice(0,-1))owner=owner[k];const key=p.at(-1);
   if(s.field==='goal')owner[key].goal=copy(v);else if(s.field==='profile')Object.assign(owner[key],v);else if(s.field==='description'||s.field==='holder')owner[key][s.field]=v;else owner[key]=copy(v);
  }
  field(label,input){const f=el('label','ui-field',label);input.setAttribute('aria-label',label);f.append(input);return f;}
  renderFields(a,value){
   a.host.replaceChildren();let current=copy(value);a.read=()=>copy(current);
   const change=()=>{a.changed=true;};
   const text=(key,label,maximum=2000)=>{const input=el('textarea');input.rows=3;input.maxLength=maximum;input.value=key?current[key]:current;input.oninput=()=>{if(key)current[key]=input.value;else current=input.value;change()};a.host.append(this.field(label,input));return input;};
   const select=(key,label,values,optional=false)=>{const input=el('select');for(const [v,t]of values){const option=el('option','',t);option.value=v;input.append(option)}input.value=current[key]||'';input.required=!optional;input.onchange=()=>{if(optional&&!input.value)delete current[key];else current[key]=input.value;change()};a.host.append(this.field(label,input));};
   const number=(key,label,min,max)=>{const input=el('input');input.type='number';input.min=min;input.max=max;input.step='any';input.required=true;input.value=current[key]??(key==='cost'?1:0);input.oninput=()=>{current[key]=input.value===''?null:Number(input.value);change()};a.host.append(this.field(label,input));};
   const options=key=>a.base[key].map(v=>[v.name,v.name]);
   if(a.spec.field==='goal'){
    const working=copy(a.base);this.put(working,a.spec,value);const editor=new global.WorldBloomDraftGoals.Editor(a.host,document,next=>{current=this.value(next,a.spec);change()});
    const render=editor.render.bind(editor);editor.render=next=>{render(next);const card=Array.from(a.host.querySelectorAll('.wd-person')).find(card=>card.querySelector('h3')?.textContent===a.spec.entity);a.host.replaceChildren();if(card){for(const c of Array.from(card.children).slice(0,6))c.remove();a.host.append(card)}for(const control of a.host.querySelectorAll('button,input,textarea,select'))if(control.disabled)control.dataset.fieldLocked='true';};editor.render(working);a.read=()=>this.value(editor.draft,a.spec);
   }else if(a.spec.field==='profile'){text('description','人物の紹介');text('personality','人物像');select('entry','開始位置',options('places'));}
   else if(a.spec.field==='description')text(null,'場所の説明');
   else if(a.spec.field==='holder'){current={holder:current};select('holder','持っている人物',options('people'));a.read=()=>current.holder;}
   else if(a.spec.field==='story')text(null,'初期物語',8000);
   else if(a.spec.field==='roles'){select('protagonist','主人公',options('people'));select('antagonist','相手役',options('people'));}
   else if(a.spec.field==='relation'){text('label','関係のラベル',120);number('affinity','親しさ',-1,1);number('awareness','認知',0,1);}
   else if(a.spec.field==='route'){select('from','出発地',options('places'));select('to','到着地',options('places'));number('cost','移動コスト',0.000001,10000);select('requires_item','必要な持ち物',[['','条件なし'],...options('items')],true);}
  }
  open(block,head,pencil,spec){
   if(this.disabled||this.active)return;
   const base=copy(this.options.getDraft()),dialog=el('dialog','ui-edit-dialog field-edit-dialog'),form=el('div','field-editor'),body=el('div','field-editor-body');form.dataset.fieldEditor=spec.field;const a={block,head,pencil,spec,base,dialog,form,body,pending:false,changed:false,preview:null,alive:true};
   this.active=a;
   const modalHead=el('div','ui-section-head field-dialog-head'),heading=el('h2','',(spec.entity?spec.entity+' / ':'')+names[spec.field]);heading.id='field-dialog-title-'+ ++modalNumber;dialog.setAttribute('aria-labelledby',heading.id);
   const dismiss=this.button('×',()=>this.close());dismiss.dataset.editCancel='';dismiss.setAttribute('aria-label','編集をキャンセルして閉じる');dismiss.classList.add('field-dialog-close');modalHead.append(heading,dismiss);
   const badge=el('p','field-edit-badge','編集中');a.badge=badge;
   const helper=el('p','ui-hint','この項目を直接書き換えるか、AIで候補を入力できます。OKを押すまで下案には反映しません。');
   a.host=el('div','field-inputs');a.status=el('p','field-status');a.status.setAttribute('role','status');a.error=el('p','ui-error');a.error.setAttribute('role','alert');
   const aiArea=el('section','field-ai-area');aiArea.hidden=true;a.aiArea=aiArea;const prompt=el('textarea');prompt.rows=3;prompt.maxLength=4000;prompt.placeholder='どのように直したいかを入力してください';a.prompt=prompt;
   const aiButton=this.button('AIで修正',()=>{aiArea.hidden=!aiArea.hidden;if(!aiArea.hidden)prompt.focus({preventScroll:true})});aiButton.classList.add('field-ai-toggle');
   const ask=this.button(this.options.demo?'保存済みの候補を入力':'候補を入力する',()=>this.generate(a));ask.dataset.aiGenerate='';
   aiArea.append(this.field('修正の概要',prompt),ask);
   if(this.options.demo){aiArea.append(el('p','ui-hint','操作見本です。保存済みの候補を入力します。この画面ではAIを呼び出しません。'));const link=el('a','','実AIでこの項目を試す →');link.href=this.options.liveUrl+'#'+new URLSearchParams({section:spec.section,entity:spec.entity,edit:spec.field,...(spec.peer?{peer:spec.peer}:{})});link.target='_blank';link.rel='noopener';aiArea.append(link);}
   const actions=el('div','field-edit-actions ui-edit-dialog-actions');a.ok=this.button('これでOK',()=>this.accept(a));a.ok.classList.add('ui-button--confirm');a.redo=this.button('作り直す',()=>this.generate(a));a.redo.hidden=true;
   const cancel=this.button('キャンセル',()=>this.close());cancel.dataset.editCancel='';actions.append(a.ok,a.redo,cancel);
   body.append(badge,helper,aiButton,aiArea,a.status,a.host,a.error);form.append(body,actions);dialog.append(modalHead,form);block.append(dialog);
   dialog.addEventListener('cancel',event=>{event.preventDefault();this.close();});
   dialog.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const controls=Array.from(dialog.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')).filter(e=>!e.disabled&&e.tabIndex>=0&&e.getClientRects().length);if(!controls.length){event.preventDefault();return;}const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}});
   dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)this.close();});
   this.renderFields(a,this.value(base,spec));this.setDisabled(this.disabled);dialog.showModal();a.host.querySelector('textarea,input,select')?.focus({preventScroll:true});body.scrollTop=0;
  }
  dispose(a){a.alive=false;if(a.dialog.open)a.dialog.close();a.dialog.remove();if(this.active===a)this.active=null;this.setDisabled(this.disabled);}
  focusPencil(spec){this.pencils.find(b=>b.fieldSpec.field===spec.field&&b.fieldSpec.entity===spec.entity&&b.fieldSpec.peer===spec.peer)?.focus({preventScroll:true});}
  close(){const a=this.active;if(!a||a.committing)return;this.dispose(a);a.pencil.focus({preventScroll:true});}
  async generate(a){
   if(!a.alive||a.pending)return;if(!a.prompt.value.trim()){a.error.textContent='AIに伝える修正の概要を入力してください。';a.prompt.focus();return;}
   const current=a.read();a.pending=true;a.error.textContent='';a.status.textContent=this.options.demo?'保存した候補を入力しています。':'この項目の候補をAIが作っています。';this.setDisabled(this.disabled);
   try{
    const instruction=a.prompt.value+'\n現在この項目に入力している内容（参考）：'+JSON.stringify(current);
    const result=await this.options.requestRevision({draft:a.base,target:a.spec,instruction,request_id:crypto.randomUUID().replaceAll('-','')});
    if(!a.alive)return;if(result.state!=='ready')throw Error(Object.values(result.errors||{}).join('／')||'候補を作れませんでした。');
    a.preview=result;this.renderFields(a,this.value(result.candidate,a.spec));a.changed=true;a.redo.hidden=false;a.aiArea.hidden=true;a.badge.textContent='候補を確認中';a.status.textContent='候補を入力しました。手で直すこともできます。「これでOK」「作り直す」「キャンセル」から選んでください。';a.body.scrollTop=0;
   }catch(error){if(a.alive){a.error.textContent=error.message;a.status.textContent='入力した内容は残っています。';}}
   finally{a.pending=false;if(a.alive)this.setDisabled(this.disabled);}
  }
  async accept(a){
   if(!a.alive||a.pending)return;if(Array.from(a.host.querySelectorAll('input,textarea,select')).some(e=>!e.reportValidity()))return;
   const candidate=copy(a.base);this.put(candidate,a.spec,a.read());
   if(candidate.protagonist===candidate.antagonist){a.error.textContent='主人公と相手役は別の人物を選んでください。';return;}
   if(a.spec.field==='route'){const r=candidate.routes[Number(a.spec.entity)];if(r.from===r.to||candidate.routes.some((other,i)=>i!==Number(a.spec.entity)&&r.from===other.from&&r.to===other.to)){a.error.textContent='同じ場所への経路や、同じ向きの経路の重複は使えません。';return;}}
   if(!a.preview||this.options.demo){this.dispose(a);this.options.onChange(candidate,a.spec.field==='roles'?{...a.spec,section:'people'}:a.spec);this.focusPencil(a.spec);return;}
   a.pending=true;a.committing=true;a.status.textContent='候補と編集した内容を確認して反映しています。';a.error.textContent='';this.setDisabled(this.disabled);
   try{await this.options.applyRevision(a.preview,a.base,candidate);this.dispose(a);this.options.refresh?.();this.focusPencil(a.spec);}
   catch(error){if(a.alive){a.error.textContent=error.message;a.status.textContent='反映できませんでした。入力内容を確認してください。';}}
   finally{a.pending=false;a.committing=false;if(a.alive)this.setDisabled(this.disabled);}
  }
 }
 global.WorldBloomDraftEditing=Editing;
})(window);
