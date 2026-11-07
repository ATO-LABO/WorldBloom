/* In-place money editing with one revision-checked, atomic save. */
(function(global){
'use strict';
const clone=v=>structuredClone(v),same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
function field(host,label,value,{number=false,nullable=false}={}){const wrap=node('label','ui-field'),input=node('input');wrap.append(node('span','ui-field-label',label));input.setAttribute('aria-label',label);input.value=value??'';if(number){input.type='number';input.min=0;input.max=1000000000;input.step=1;input.required=!nullable;}else{input.maxLength=80;input.required=true;}wrap.append(input);host.append(wrap);return input;}
function select(host,label,choices,value){const wrap=node('label','ui-field'),input=node('select');wrap.append(node('span','ui-field-label',label));input.setAttribute('aria-label',label);for(const[id,title,disabled]of choices){const option=node('option','',title);option.value=id;option.disabled=!!disabled;input.append(option);}input.value=value??'';wrap.append(input);host.append(wrap);return input;}
function incomeAssets(data){const payment=new Set(data.items.flatMap(row=>Object.keys(row.definition.price||{})));return data.items.filter(row=>row.name===data.economy.currency.asset||payment.has(row.name)||row.definition.sources?.some(source=>source.type==='labor'));}
function incomeBase(data){const entries=[];for(const row of incomeAssets(data)){(row.definition.sources||[]).forEach((source,index)=>{if(source.type==='labor')entries.push({kind:'labor',asset:row.name,index,amount:String(source.amount??1)});});for(const reward of row.definition._rewards||[])entries.push({kind:'trial',asset:row.name,id:reward.id,amount:String(reward.amount??1)});}return entries;}
function base(data){const s=data.economy,c=s.currency,custom=c.custom||(c.preset==='custom'?{name:c.name,unit:c.unit}:{name:'',unit:''});return {currency:{preset:c.preset,asset:c.asset||'',customName:custom.name,customUnit:custom.unit},income:incomeBase(data),meal:{name:s.meal.name,amount:s.meal.amount==null?'':String(s.meal.amount)},items:Object.fromEntries(data.items.filter(r=>r.name!==c.asset).map(r=>[r.name,{trade:r.definition.monetary_trade??!!r.definition.shop_zone,amount:r.definition.price?.[c.asset]==null?'':String(r.definition.price[c.asset]),shop:r.definition.shop_zone?'place:'+r.definition.shop_zone:''}]))};}
const amount=value=>value===''?null:Number(value);
const moneyTabs=[['currency','通貨設定'],['income','資産の入手方法'],['trades','品物の金銭取引']];
const tabFromHash=()=>{const value=new URLSearchParams(location.hash.slice(1)).get('tab');return value==='reference'?'trades':moneyTabs.some(([id])=>id===value)?value:'currency';};
function render(root,workspace){
 const data=workspace.data,s=data.economy;if(!s)return;
 let session=workspace.moneyInline;
 if(!session){session=workspace.moneyInline={base:base(data),draft:base(data),tab:tabFromHash(),dirty:false,busy:false,blocked:false,message:'',state(){return {dirty:this.dirty,busy:this.busy};},discard(){this.dirty=false;this.draft=clone(this.base);workspace.notifyState?.();}};}
 const fresh=base(data);
 if(!session.dirty&&!session.busy){session.base=fresh;session.draft=clone(fresh);}else if(!same(fresh,session.base)&&!session.busy){session.blocked=true;session.message='保存済みの金銭設定が変わりました。入力を控え、再読み込みして確認してください。';}
 if(session.scrollBody){session.scrolls??={};session.scrolls[session.tab]=session.scrollBody.scrollTop||0;}session.cleanup?.();
 const draft=session.draft,unit=s.configured?s.currency.unit:s.currency.asset||'通貨未設定',form=node('form','ui-settings-frame ui-money-theme'),error=node('p','ui-error ui-settings-error'),status=node('p','ui-settings-status');form.dataset.moneyInline='';form.noValidate=true;error.setAttribute('role','alert');error.setAttribute('tabindex','-1');error.textContent=session.message;error.hidden=!session.message;status.setAttribute('role','status');root.append(form);
 const header=node('header','ui-settings-header'),heading=node('div','ui-settings-title'),back=node('a','ui-button','← 行動の種類'),title=node('h1','','金銭設定'),body=node('div','ui-settings-body');back.href='/worlds/'+encodeURIComponent(data.id)+'?view=actions#tab=types';back.dataset.economyBack='';back.setAttribute('aria-label','行動の種類に戻る');const nameGroup=node('div','ui-settings-name');nameGroup.append(title);heading.append(back,nameGroup);header.append(heading);body.dataset.moneyBody='';form.append(header,body);session.scrollBody=body;
 const helpers=[];session.cleanup=()=>{session.frameLayout?.disconnect();helpers.forEach(helper=>helper.destroy());};
 const help=(host,label,text)=>{let helper=global.WorldBloomHelp?.create(label,text,{theme:'ui-money-theme'});if(!helper){const button=node('button','ui-help-button','?');button.type='button';button.dataset.uiHelp='';button.setAttribute('aria-label',label);button.title=text;helper={button,destroy(){}};}helpers.push(helper);const target=host.querySelector('.ui-heading-with-help')||host.querySelector('.ui-settings-card-heading')||host;target.append(helper.button);return helper;};
 help(nameGroup,'金銭設定の説明','3つのタブの変更をまとめて保存し、次の実行から使います。過去の実行結果は変更しません。'+(data.economyEnabled?'購入時は資産と価格を照合し、支払った分を差し引きます。':'金銭機能は無効です。価格・初期資産・入手方法の保存済み値は保持されています。'));
 if(!data.editable)body.append(node('p','ui-notice',data.economyEnabled?'閲覧専用です。保存済みの設定を確認できます。':'購入時の支払いが無効のため閲覧専用です。設定値は保持されています。'));
 const card=title=>{const section=node('section','wa-topic');if(title){const head=node('div','ui-heading-with-help');head.append(node('h2','',title));section.append(head);}body.append(section);return section;};
 const currency=card('通貨'),currencyRow=node('div','ui-field-row');currency.append(currencyRow);const preset=select(currencyRow,'通貨',[['JPY','円'],['USD','ドル（米ドル）'],['CNY','元（人民元）'],['EUR','ユーロ'],['custom','その他']],draft.currency.preset),custom=field(currencyRow,'その他の通貨単位',draft.currency.customUnit),customWrap=custom.parentElement||custom.parent;
 preset.required=true;custom.placeholder='例：ゴールド';let asset=null;
 if(!s.configured&&s.asset_choices.length>1){asset=select(currency,'支払いに使う既存資産',[['','選択してください'],...s.asset_choices.map(n=>[n,n])],draft.currency.asset);asset.required=true;}
 help(currency,'通貨の説明','通貨を切り替えても残高・価格は換算・消去しません。その他に入力した通貨単位も保持します。');
 if(!s.configured)currency.append(node('p','ui-hint','先に通貨を保存すると、価格を直接入力できます。開始所持金は人物で設定します。'));
 const balanceNotice=node('p','ui-notice');balanceNotice.dataset.moneyBalanceMoved='';balanceNotice.append(node('span','','開始所持金は人物で設定します。'),workspace.personBalanceLink());currency.append(balanceNotice);
 const tabs=node('div','ui-tabs items-tabs ui-settings-tabs'),panels={},buttons={};tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','金銭設定の項目');body.remove();form.append(tabs,body);
 for(const[id,title]of moneyTabs){const button=node('button','ui-button',title),panel=node('section');button.type='button';button.id='money-tab-'+id;button.dataset.moneyTab=id;button.setAttribute('role','tab');button.setAttribute('aria-controls','money-panel-'+id);panel.id='money-panel-'+id;panel.dataset.moneyPanel=id;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);tabs.append(button);body.append(panel);buttons[id]=button;panels[id]=panel;button.onclick=()=>{if(session.busy)return;selectTab(id);button.focus();};}
 function selectTab(id,write=true){if(!panels[id])id='currency';session.scrolls??={};const changed=id!==session.tab;if(changed)session.scrolls[session.tab]=body.scrollTop||0;session.tab=id;for(const key of Object.keys(panels)){panels[key].hidden=key!==id;buttons[key].setAttribute('aria-selected',String(key===id));buttons[key].tabIndex=key===id?0:-1;}if(changed)body.scrollTop=session.scrolls[id]||0;if(write)history.replaceState(null,'',session.hash());}
 panels.currency.append(currency);
 session.hash=()=>'#economy&tab='+session.tab;const canonicalize=()=>{if(['reference','balance'].includes(new URLSearchParams(location.hash.slice(1)).get('tab')))history.replaceState(null,'',session.hash());};session.selectFromHash=()=>{if(session.busy){history.replaceState(null,'',session.hash());return;}const next=tabFromHash(),changed=next!==session.tab;selectTab(next,false);canonicalize();if(changed)buttons[next].focus();};session.incomePanel=panels.income;selectTab(session.tab,false);canonicalize();
 const incomeControls=[],incomeRoot=panels.income,assets=incomeAssets(data);
 const incomeHeading=node('div','ui-heading-with-help');incomeHeading.append(node('h2','','資産の入手方法'));incomeRoot.append(incomeHeading,node('p','ui-hint','入手量は1〜1000000000の整数です。'));help(incomeRoot,'資産の入手方法の説明','登録済みの資産を、働く行動または条件付きの報酬で得る方法です。入手量は1〜1000000000の整数です。同じ場所の労働経路は、働く行動でそれぞれ進みます。');
 for(const row of assets){
  const section=node('section','wa-topic'),heading=node('h3','',row.definition.display_name||row.name);incomeRoot.append(section);section.append(workspace.topic(row,'sources',heading));
  const entries=draft.income.map((entry,index)=>({entry,index})).filter(({entry})=>entry.asset===row.name);
  if(!entries.length)section.append(node('p','ui-hint','労働・人物からの報酬は未設定です。'));
  for(const {entry,index}of entries){
   const group=node('section','items-source-editor'),isNew=entry.kind==='add-labor';group.dataset.moneyIncome=String(index);section.append(group);let zone=null,count=null,max=null;
   if(isNew){select(group,'実行する行動',[['labor','働く']],'labor');zone=select(group,row.name+'の働く場所（追加 '+(index+1)+'）',data.places.map(name=>[name,name]),entry.zone);zone.required=true;count=field(group,'入手に必要な行動回数（追加 '+(index+1)+'）',entry.count,{number:true});count.min=1;count.max=10000;max=field(group,'人物ごとの受取回数上限（追加 '+(index+1)+'）',entry.max,{number:true});max.max=10000;
    const remove=node('button','ui-button','追加した経路を取り消す');remove.type='button';remove.onclick=()=>{if(session.busy||session.blocked)return;capture();draft.income.splice(index,1);session.dirty=!same(draft,session.base);workspace.render();};group.append(remove);
   }else if(entry.kind==='labor'){
    const source=row.definition.sources[entry.index];group.append(node('p','','働く：'+source.zone+'。'+(source.count??1)+'回働くごとに受け取り、人物ごとの受取上限 '+(source.max??1)+'回。'+(source.stamina?'行動ごとの体力消費 '+source.stamina+'。':'')));
   }else{
    const reward=row.definition._rewards.find(value=>value.id===entry.id);group.append(node('p','','報酬：'+reward.giver+'と同じ場所で、人物ごとに1回。'+(reward.requires?.item?'必要な所持品：'+reward.requires.item+'。':'')+(reward.requires?.stance!==undefined?'渡す人物からの関係：'+reward.requires.stance+'以上。':'')+(reward.mode==='transfer'?'渡す人物の在庫から受け取ります。在庫不足では受け取れません。':'条件を満たすと発生します。')));
   }
   const quantity=field(group,row.name+'の入手量（'+(entry.kind==='trial'?'報酬':'労働')+' '+(index+1)+'）',entry.amount,{number:true});quantity.min=1;quantity.dataset.moneyIncomeAmount=String(index);group.append(node('small','ui-hint',row.name===s.currency.asset?unit:'個'));
   incomeControls.push({entry,quantity,zone,count,max});if(!s.configured)group.querySelectorAll('input,select,button').forEach(control=>control.disabled=true);
  }
 }
 const addBox=node('section','wa-topic'),assetChoice=select(addBox,'入手経路を追加する資産',assets.map(row=>[row.name,row.definition.display_name||row.name]),assets[0]?.name),addLabor=node('button','ui-button','働いて得る経路を追加');addLabor.type='button';addLabor.dataset.moneyAddLabor='';addLabor.disabled=!s.configured||!assets.length||!data.places.length;const addHeading=node('div','ui-heading-with-help');addHeading.append(node('h3','','入手方法を追加'));addBox.prepend(addHeading);addBox.append(addLabor);help(addBox,'入手方法を追加する操作の説明','資産と場所は登録済みの候補から選びます。人物の報酬条件は、各資産の入手経路編集から設定できます。');incomeRoot.append(addBox);
 addLabor.onclick=()=>{if(addLabor.disabled||session.busy||session.blocked)return;capture();const addedIndex=draft.income.length;draft.income.push({kind:'add-labor',asset:assetChoice.value,zone:data.places[0]||'',amount:'1',count:'1',max:'1'});session.dirty=true;workspace.render();workspace.root.querySelector('[data-money-income-amount="'+addedIndex+'"]')?.focus();};
 const reference=card('相場の基準'),referenceRow=node('div','ui-field-row money-reference-row');reference.dataset.moneyReference='';reference.append(referenceRow);panels.trades.append(reference);const mealName=field(referenceRow,'基準にする食事の名称',draft.meal.name),mealAmount=field(referenceRow,'1食の基準価格（空欄は未設定）',draft.meal.amount,{number:true,nullable:true});mealName.disabled=mealAmount.disabled=!s.configured;mealName.closest('label').querySelector('.ui-field-label').textContent='食事名';mealAmount.closest('label').querySelector('.ui-field-label').textContent='価格';
 const apply=node('button','ui-button','目安を入れる'),notice=node('p','ui-hint');apply.type='button';apply.dataset.moneyPresetApply='';apply.disabled=!s.configured;
 referenceRow.append(node('span','ui-field-unit',unit),apply);reference.append(notice);reference.querySelector('.ui-heading-with-help').append(node('small','ui-hint','物語用の目安'));help(reference,'相場の基準の説明','物語用の仮の目安・編集可能です。円1000、米ドル10、人民元30、ユーロ10。実在市場の相場や公式平均ではありません。入力済みの価格は上書きせず、品物価格も自動設定しません。');
 const tradesCard=node('section','ui-settings-card money-trades-card'),tradesHead=node('header','ui-settings-card-heading');tradesHead.append(node('h2','','品物の金銭取引'),node('p','ui-hint','空欄は未設定、0は無料。取引オフでも価格を保持します。'));tradesCard.append(tradesHead);panels.trades.append(tradesCard);
 const table=node('table','items-table money-trades-table'),head=node('thead'),tr=node('tr');for(const label of ['品物','取引','価格','購入先']){const th=node('th','',label);th.scope='col';tr.append(th);}head.append(tr);table.append(head);const tableBody=node('tbody'),rows=[];
 for(const row of data.items.filter(r=>r.name!==s.currency.asset)){
  const value=draft.items[row.name],line=node('tr'),title=node('th','',row.definition.display_name||row.name);title.scope='row';line.dataset.moneyRow=row.name;line.dataset.moneyTradeState=value.trade?'allowed':'blocked';line.append(title);
  const tradeCell=node('td'),label=node('label','ui-inline ui-switch-target'),trade=node('input','ui-switch');trade.type='checkbox';trade.setAttribute('role','switch');trade.checked=value.trade;trade.dataset.moneyTrade=row.name;trade.setAttribute('aria-label',row.name+'の金銭取引を許可');label.prepend(trade);tradeCell.append(label);line.append(tradeCell);
  const priceCell=node('td'),priceWrap=node('div','ui-money-price'),price=field(priceWrap,row.name+'の価格（空欄は未設定、0は無料）',value.amount,{number:true,nullable:true});
  price.placeholder='—';priceCell.append(priceWrap);priceWrap.append(node('span','ui-field-unit',unit));price.closest('label').classList.add('ui-field--table');const other=Object.entries(row.definition.price||{}).filter(([id])=>id!==s.currency.asset);if(other.length)priceCell.append(node('small','ui-hint','ほかの資産の価格を保持：'+other.map(([n,q])=>n+' ×'+q).join('、')));line.append(priceCell);
  const shopCell=node('td'),shop=select(shopCell,row.name+'の購入先',[['','未設定'],...data.places.map(n=>['place:'+n,'場所：'+n]),...data.people.map(n=>['person:'+n,'人物：'+n+'（人物購入は未対応）',true])],value.shop);shop.closest('label').classList.add('ui-field--table');line.append(shopCell);
  trade.disabled=price.disabled=shop.disabled=!s.configured;shop.required=trade.checked;trade.onchange=()=>{shop.required=trade.checked;line.dataset.moneyTradeState=trade.checked?'allowed':'blocked';capture();};rows.push({name:row.name,trade,price,shop});tableBody.append(line);
 }table.append(tableBody);const scroll=node('div','items-table-scroll');scroll.append(table);tradesCard.append(scroll);help(tradesCard,'取引と購入先の説明','人物候補は登録済み人物です。人物からの購入には在庫と代金の受渡し条件の追加が必要なため、現在は場所購入のみ保存できます。売却行動は含みません。');
 const actions=node('div','ui-settings-actions'),save=node('button','ui-button ui-button--confirm','変更を保存'),reset=node('button','ui-button','変更を戻す');save.type='submit';save.dataset.moneySave='';reset.type='button';reset.dataset.moneyReset='';actions.append(status,reset,save);header.append(actions,error);
 const visibility=()=>{customWrap.hidden=preset.value!=='custom';custom.disabled=preset.value!=='custom';custom.required=preset.value==='custom';};visibility();
 function capture(){for(const control of incomeControls){control.entry.amount=control.quantity.value;if(control.zone){control.entry.zone=control.zone.value;control.entry.count=control.count.value;control.entry.max=control.max.value;}}draft.currency.preset=preset.value;draft.currency.customUnit=custom.value;if(asset)draft.currency.asset=asset.value;draft.meal={name:mealName.value,amount:mealAmount.value};for(const row of rows)draft.items[row.name]={trade:row.trade.checked,amount:row.price.value,shop:row.shop.value};session.dirty=!same(draft,session.base);sync();}
 function sync(){if(session.busy)helpers.forEach(helper=>helper.close?.(false));for(const button of Object.values(buttons))button.disabled=session.busy;save.disabled=!data.editable||session.busy||session.blocked||s.configured&&!session.dirty;reset.disabled=!data.editable||session.busy||session.blocked||!session.dirty;status.textContent=session.busy?'保存中…':session.blocked?'再読み込みが必要':!data.editable?'閲覧専用':session.dirty?'未保存の変更あり':'保存済み';status.dataset.moneyState=session.busy?'saving':session.blocked?'blocked':!data.editable?'readonly':session.dirty?'dirty':'saved';error.hidden=!error.textContent;back.setAttribute('aria-disabled',String(session.busy));workspace.root.querySelectorAll('[data-items-edit]').forEach(button=>{if(button.dataset.moneyOriginalDisabled===undefined)button.dataset.moneyOriginalDisabled=String(button.disabled);button.disabled=button.dataset.moneyOriginalDisabled==='true'||session.dirty||session.busy||session.blocked;});workspace.notifyState?.();}
 preset.onchange=()=>{visibility();capture();};form.addEventListener('input',event=>{event.target.setCustomValidity?.('');capture();});form.addEventListener('change',capture);
 apply.onclick=()=>{if(apply.disabled||session.busy)return;const current=data.presets.find(p=>p.id===preset.value);if(mealAmount.value!==''){notice.textContent='入力済みの価格は変更しません。';return;}if(current?.meal==null){notice.textContent='その他の通貨は基準価格を入力してください。';return;}mealAmount.value=String(current.meal);notice.textContent='物語用の仮の目安を入力しました。編集してから保存できます。';capture();};
 reset.onclick=()=>{if(session.busy||session.blocked||session.dirty&&!confirm('未保存の金銭設定を元に戻しますか？'))return;session.base=base(workspace.data);session.draft=clone(session.base);session.dirty=false;session.message='';workspace.render();workspace.root.querySelector('[data-money-tab="'+session.tab+'"]')?.focus();};
 function revealError(control,message){const panel=control.closest('[data-money-panel]');if(panel)selectTab(panel.dataset.moneyPanel);error.textContent=message;error.hidden=false;control.focus();control.reportValidity?.();}
 function valid(){for(const input of [custom,mealName])input.setCustomValidity?.(!input.disabled&&!input.value.trim()?'空白以外の文字を入力してください。':'');const invalid=[...form.querySelectorAll('input,select')].find(input=>!input.disabled&&input.checkValidity&&!input.checkValidity());if(!invalid)return true;revealError(invalid,'「'+invalid.getAttribute('aria-label')+'」を確認してください。');return false;}
 form.onsubmit=async event=>{
  event.preventDefault();if(!data.editable||session.busy||session.blocked)return;capture();if(!valid())return;const changes=[];
  if(!s.configured||!same(draft.currency,session.base.currency)){
   const option=data.presets.find(p=>p.id===draft.currency.preset),customName=draft.currency.customName||draft.currency.customUnit;
   const values={preset:option.id,name:option.id==='custom'?customName:option.name,unit:option.id==='custom'?draft.currency.customUnit:option.unit,asset:draft.currency.asset};
   if(draft.currency.customUnit.trim())values.custom={name:customName,unit:draft.currency.customUnit};changes.push({operation:'economy-currency',target:null,values});
  }
  if(s.configured){
    const entries=draft.income.filter(entry=>entry.kind==='add-labor'||!session.base.income.some(prior=>same(prior,entry))).map(entry=>({...entry,amount:amount(entry.amount),...(entry.kind==='add-labor'?{count:amount(entry.count),max:amount(entry.max)}:{})}));if(entries.length)changes.push({operation:'economy-income',target:null,values:{entries}});
   if(!same(draft.meal,session.base.meal))changes.push({operation:'economy-reference',target:null,values:{name:draft.meal.name,amount:amount(draft.meal.amount)}});
   for(const[name,value]of Object.entries(draft.items))if(!same(value,session.base.items[name])){if(value.shop.startsWith('person:')){revealError(rows.find(row=>row.name===name).shop,'人物からの購入は現在保存できません。登録済みの場所を選んでください。');return;}changes.push({operation:'economy-item',target:name,values:{trade:value.trade,amount:amount(value.amount),shop_zone:value.shop.startsWith('place:')?value.shop.slice(6):null}});}
  }
  if(!changes.length)return;const active=document.activeElement,key=active?.getAttribute?.('aria-label');session.busy=true;session.message='';error.textContent='';const disabled=new Map([...workspace.root.querySelectorAll('input,select,button')].map(n=>[n,n.disabled]));for(const[n]of disabled)n.disabled=true;sync();
  try{await workspace.save('economy-settings',null,{changes});session.busy=false;session.dirty=false;session.base=base(workspace.options.getData());session.draft=clone(session.base);workspace.render();const focus=key?[...workspace.root.querySelectorAll('input,select')].find(n=>n.getAttribute('aria-label')===key):null;(!focus?.disabled&&focus||workspace.root.querySelector('[data-money-tab="'+session.tab+'"]'))?.focus();}
  catch(e){session.busy=false;session.message=e.message||'保存できませんでした。';session.blocked=!!e.blocked;error.textContent=session.message+(session.blocked?' 入力を控えてから再読み込みして確認してください。':'');for(const[n,value]of disabled)n.disabled=value;sync();error.focus();}
 };session.sync=sync;if(!data.editable)form.querySelectorAll('input,select,button').forEach(n=>{if(n.dataset.moneyTab===undefined&&n.dataset.uiHelp===undefined)n.disabled=true;});sync();body.scrollTop=session.scrolls?.[session.tab]||0;session.frameLayout=global.WorldBloomSettingsFrame?.mount(form,{header,tabs,body});
}
function mount(root,{getWorld,editable,save,onState}){
 const getData=()=>{
  const model=getWorld(),world=model.world,economy=model.economy_state,enabled=model.mechanic_features.includes('budgeted_purchases');
  const items=(world.items||[]).map(item=>typeof item==='string'?{name:item}:item).map(item=>({
   name:item.name,
   definition:{...item,_rewards:(world.trials||[]).filter(trial=>trial.grants?.item===item.name).map(trial=>({
    id:trial.id||trial.giver+':'+Object.keys(trial.grants).sort().map(key=>key+':'+trial.grants[key]).join(','),
    giver:trial.giver,requires:trial.requires,amount:trial.item_amount??1,mode:trial.transfer_item?'transfer':'grant'
   }))}
  }));
  return {id:model.id,economy,economyEnabled:enabled,editable:editable()&&enabled,presets:model.economy_presets,
    items,places:(world.zones||[]).map(zone=>typeof zone==='string'?zone:zone.name),people:model.people.map(person=>person.id)};
 };
 const workspace={root,options:{getData},save,
  render(){this.data=getData();root.replaceChildren();render(root,this);},
  notifyState(){onState(this.moneyInline.state());},
  personBalanceLink(){
   const link=node('a','ui-button','人物の設定を開く');
   link.href='/worlds/'+encodeURIComponent(this.data.id)+'#screen=people';
   return link;
  },
  topic(row,_kind,heading){
   const link=node('a','',heading.textContent);
   link.href='/worlds/'+encodeURIComponent(this.data.id)+'#'+new URLSearchParams({screen:'items',entity:row.name});
   heading.replaceChildren(link);
   return heading;
  }
 };
 workspace.render();
 return workspace;
}
global.WorldBloomMoney={render,mount};
})(window);
