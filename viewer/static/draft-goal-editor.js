/* Human edits only: no requests, evaluation or inferred replacement objectives. */
(function(global){
  'use strict';
  const TYPES={reach:'場所への到着',acquire:'品物の入手',deliver:'品物を持って到着',
    meet:'相手との対面',befriend:'双方の親しさ',defeat:'相手の戦闘不能',survive:'指定日までの生存',discover:'情報を知る'};
  function goalValue(raw){
    if(!raw||typeof raw!=='object')return null;
    if(typeof raw.description==='string'&&Array.isArray(raw.conditions))return JSON.parse(JSON.stringify(raw));
    if(typeof raw.target==='string'&&typeof raw.deliver_to==='string')return {
      description:raw.target?raw.target+'を'+raw.deliver_to+'へ届ける':'',
      conditions:raw.target?[{type:'deliver',item:raw.target,place:raw.deliver_to}]:[]};
    return null;
  }
  let dialogNumber=0;
  class Editor{
    constructor(root,doc,onChange){this.root=root;this.doc=doc;this.onChange=onChange;this.disabled=false;this.controls=[];this.dialogId='draft-text-editor-'+(++dialogNumber);}
    node(tag,text){const n=this.doc.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
    control(node,locked=false){this.controls.push({node,locked});node.disabled=this.disabled||locked;return node;}
    field(title,node){const label=this.node('label',title);label.className='ui-field';label.append(node);return label;}
    button(text,handler,locked=false){const button=this.control(this.node('button',text),locked);button.type='button';button.className='ui-button';button.addEventListener('click',()=>{if(!button.disabled)handler();});return button;}
    select(values,value,change){
      const select=this.control(this.node('select'));
      const entries=[['',values.find(v=>v[0]==='')?.[1]||'選択してください'],...values.filter(v=>v[0]!=='')];
      if(typeof value==='string'&&value&&!entries.some(v=>v[0]===value))entries.push([value,'未登録：'+value]);
      for(const [key,label] of entries){const option=this.node('option',label);option.value=key;select.append(option);}
      select.value=typeof value==='string'?value:'';
      select.addEventListener('change',()=>{if(!select.disabled)change(select.value);});
      return select;
    }
    setDisabled(value){this.disabled=value;for(const c of this.controls)c.node.disabled=value||c.locked;}
    publish(redraw=false){
      this.onChange(JSON.parse(JSON.stringify(this.draft)));
      if(redraw)this.render(this.draft);
    }
    textDialog(title,fields,apply,opener,options={}){
      if(this.disabled)return;
      const dialog=this.node('dialog');dialog.className='ui-edit-dialog';
      dialog.setAttribute('aria-labelledby',this.dialogId);
      const form=this.node('form'),head=this.node('div');head.className='ui-section-head';
      const heading=this.node('h2',title);heading.id=this.dialogId;
      const close=()=>dialog.close();
      const dismiss=this.button('×',close);dismiss.setAttribute('aria-label','閉じる');head.append(heading,dismiss);form.append(head);
      const note=this.node('p',options.note||'保存すると下案に反映します。修正後は「下案を確認する」で再確認してください。採用するまで世界には保存しません。');note.className='ui-hint';form.append(note);
      const inputs=[];let committed=false;
      for(const field of fields){
        const type=field.type||'textarea';
        const input=type==='select'?this.select(field.options||[],field.value,()=>{}):this.control(this.node(type==='textarea'?'textarea':'input'));
        input.name=field.key;input.required=!!field.required;
        if(type==='select'&&field.emptyLabel)input.children[0].textContent=field.emptyLabel;
        if(type==='number'){
          input.type='number';input.min=String(field.min);input.max=String(field.max);input.step='any';
          input.value=typeof field.value==='number'&&Number.isFinite(field.value)?String(field.value):'';
        }else if(type!=='select'){
          if(type==='text')input.type='text';
          input.maxLength=field.maxLength??2000;input.value=typeof field.value==='string'?field.value:'';
        }
        form.append(this.field(field.label,input));inputs.push({field,input});
      }
      const error=this.node('p');error.className='ui-error';error.setAttribute('role','alert');form.append(error);
      const actions=this.node('div');actions.className='ui-edit-dialog-actions';
      actions.append(this.button('キャンセル',close));
      const save=this.control(this.node('button',options.saveLabel||'保存する'));save.type='submit';save.className='ui-button ui-button--confirm';actions.append(save);form.append(actions);
      form.addEventListener('submit',event=>{
        event.preventDefault();if(this.disabled||!form.reportValidity())return;
        const values={};
        for(const {field,input} of inputs){
          if(field.type==='number'){
            const number=Number(input.value);
            if(!input.value.trim()||!Number.isFinite(number)||number<field.min||number>field.max||(field.exclusiveMin&&number===field.min)){error.textContent=field.label+'の範囲を確認してください。';return;}
            values[field.key]=number;
          }else{
            if(field.type==='select'&&!(field.options||[]).some(v=>v[0]===input.value)){error.textContent=field.label+'を選んでください。';return;}
            values[field.key]=input.value;
          }
        }
        const message=apply(values);if(typeof message==='string'){error.textContent=message;return;}
        committed=true;this.publish();close();
      });
      dialog.addEventListener('close',()=>{
        const removed=new Set([dismiss,save,...inputs.map(v=>v.input),...actions.children]);
        this.controls=this.controls.filter(c=>!removed.has(c.node));
        dialog.remove();if(committed&&options.redraw)this.render(this.draft);const focus=opener?.isConnected?opener:options.focusAfter?.();if(focus?.isConnected)focus.focus({preventScroll:true});
      });
      dialog.append(form);this.root.append(dialog);dialog.showModal();
    }
    itemReferences(name){
      const uses=[];
      for(const person of this.draft.people){
        if(person?.goal?.target===name||(Array.isArray(person?.goal?.conditions)&&person.goal.conditions.some(c=>['acquire','deliver'].includes(c?.type)&&c.item===name)))uses.push(person.name+'の達成条件');
      }
      for(const route of this.draft.routes||[])if(route?.requires_item===name)uses.push(route.from+' → '+route.to+'の必要な持ち物');
      return uses;
    }
    collectionEditor(title,key,maximum,spec){
      const rows=this.draft[key];if(!Array.isArray(rows))return;
      const section=this.node('section');section.className='ui-card';
      const head=this.node('div');head.className='ui-section-head';head.append(this.node('h3',title));
      const focusAfter=()=>this.controls.find(c=>c.node.getAttribute('aria-label')===spec.addLabel)?.node;
      const options={redraw:true,focusAfter};
      const add=this.button('＋ '+spec.addLabel,()=>this.textDialog(spec.addLabel,spec.fields(null),values=>{
        const result=spec.value(values,null);if(typeof result==='string')return result;
        rows.push(result);
      },add,options),rows.length>=maximum||!spec.available());add.setAttribute('aria-label',spec.addLabel);head.append(add);section.append(head);
      const hint=this.node('p',spec.hint);hint.className='ui-hint';section.append(hint);
      if(!rows.length)section.append(this.node('p',spec.empty));
      rows.forEach(row=>{
        if(!row||typeof row!=='object'){section.append(this.node('p','形式を詳しい修正欄で確認してください。'));return;}
        const line=this.node('div');line.className='wd-relation-row';
        const copy=this.node('div');copy.append(this.node('h4',spec.title(row)));
        const detail=this.node('p',spec.detail(row));detail.className='wd-copy';copy.append(detail);
        const actions=this.node('div');actions.className='wd-relation-actions';
        const edit=this.button('✎ 編集',()=>this.textDialog(spec.title(row)+'を編集',spec.fields(row),values=>{
          const result=spec.value(values,row);if(typeof result==='string')return result;
          Object.assign(row,result);
        },edit,{...options,focusAfter:()=>this.controls.find(c=>c.node.getAttribute('aria-label')===spec.title(row)+'を編集')?.node||focusAfter()}));edit.setAttribute('aria-label',spec.title(row)+'を編集');
        const remove=this.button('削除',()=>this.textDialog(spec.title(row)+'を削除',[],()=>{
          const message=spec.beforeRemove?.(row);if(message)return message;
          rows.splice(rows.indexOf(row),1);
        },remove,{...options,saveLabel:'削除する',note:spec.removeNote}));remove.setAttribute('aria-label',spec.title(row)+'を削除');
        actions.append(edit,remove);line.append(copy,actions);section.append(line);
      });this.root.append(section);
    }
    itemsEditor(){
      const people=this.draft.people.filter(p=>typeof p?.name==='string').map(p=>[p.name,p.name]);
      this.collectionEditor('品物','items',8,{
        addLabel:'品物を追加',available:()=>people.length>0,
        hint:'品物の名前と開始時点の所持者を設定します。名前を変えると、達成条件と経路の参照も更新します。目標や導入文の文章は必要に応じて修正してください。',
        empty:'品物は未設定です。必要な場合は「品物を追加」から設定できます。',
        title:item=>item.name||'名前未入力の品物',detail:item=>'所持者：'+(item.holder||'未設定'),
        fields:item=>[
          {key:'name',label:'品物の名前',type:'text',maxLength:120,required:true,value:item?.name||''},
          {key:'holder',label:'開始時点の所持者',type:'select',options:people,required:true,value:item?.holder||''}
        ],
        value:(values,item)=>{
          const name=values.name.trim();if(!name||name.length>120)return '品物の名前は1〜120文字で入力してください。';
          if(this.draft.items.some(other=>other!==item&&other?.name===name))return '同じ名前の品物があります。別の名前にしてください。';
          if(item&&item.name!==name){
            for(const person of this.draft.people){
              if(person?.goal?.target===item.name)person.goal.target=name;
              for(const c of person?.goal?.conditions||[])if(['acquire','deliver'].includes(c?.type)&&c.item===item.name)c.item=name;
            }
            for(const route of this.draft.routes||[])if(route?.requires_item===item.name)route.requires_item=name;
          }
          return {name,holder:values.holder};
        },
        beforeRemove:item=>{const uses=this.itemReferences(item.name);return uses.length?'この品物は'+uses.join('、')+'で使われています。先に参照先を変更・解除してください。':null;},
        removeNote:'この品物を下案から削除します。達成条件や経路で使っている品物は、先に参照先を変更・解除してください。採用する前に下案を再確認してください。'
      });
    }
    routesEditor(){
      const routes=this.draft.routes;if(!Array.isArray(routes))return;
      const places=(this.draft.places||[]).filter(p=>typeof p?.name==='string');
      const pairs=row=>places.flatMap((from,si)=>places.filter(to=>to.name!==from.name&&!routes.some(r=>r!==row&&r?.from===from.name&&r?.to===to.name)).map(to=>[si+':'+places.indexOf(to),from.name+' → '+to.name]));
      this.collectionEditor('経路','routes',40,{
        addLabel:'経路を追加',available:()=>pairs(null).length>0,
        hint:'出発地 → 到着地の向きごとに設定します。復路は別に追加・編集してください。移動コストと必要な持ち物は通常の世界設定と同じ項目です。',
        empty:'経路は未設定です。「経路を追加」から場所をつなげられます。',
        title:route=>(route.from||'未設定')+' → '+(route.to||'未設定')+' の経路',
        detail:route=>(route.requires_item?route.requires_item+'が必要':'持ち物の条件なし')+' ・ コスト '+(route.cost??1),
        fields:route=>[
          {key:'pair',label:'経路の向き（出発地 → 到着地）',type:'select',options:pairs(route),required:true,value:route?places.findIndex(p=>p.name===route.from)+':'+places.findIndex(p=>p.name===route.to):''},
          {key:'cost',label:'移動コスト（0より大きく10000以下）',type:'number',min:0,max:10000,exclusiveMin:true,required:true,value:route?.cost??1},
          {key:'requires_item',label:'必要な持ち物（条件なしも選べます）',type:'select',emptyLabel:'条件なし',options:[['','条件なし'],...(this.draft.items||[]).filter(i=>typeof i?.name==='string').map(i=>[i.name,i.name])],value:route?.requires_item||''}
        ],
        value:(values,row)=>{
          const [si,ti]=values.pair.split(':').map(Number),from=places[si],to=places[ti];
          if(!from||!to||from===to||routes.some(r=>r!==row&&r?.from===from.name&&r?.to===to.name))return '同じ向きの経路の重複や、自分への経路は使えません。';
          const result={from:from.name,to:to.name,cost:values.cost};
          if(values.requires_item)result.requires_item=values.requires_item;
          if(row&&!values.requires_item)delete row.requires_item;
          return result;
        },
        removeNote:'この向きの経路を下案から削除します。復路は変わりません。採用する前に下案を再確認してください。'
      });
    }
    relationsEditor(){
      const section=this.node('section');section.className='ui-card';
      const head=this.node('div');head.className='ui-section-head';head.append(this.node('h3','人物間の関係'));
      const body=this.node('div');let buttons=[],focusAfter=null;
      const people=this.draft.people.filter(p=>p&&typeof p.name==='string');
      const pairs=[];
      people.forEach((source,si)=>people.forEach((target,ti)=>{
        if(source.name!==target.name&&Array.isArray(source.relations)&&source.relations.length<7&&!source.relations.some(r=>r&&r.person===target.name))pairs.push([si+':'+ti,source.name+' → '+target.name]);
      }));
      const fields=relation=>[
        {key:'label',label:'関係ラベル（仲間・家族・敵対など／空欄で解除）',type:'text',maxLength:120,value:relation.label},
        {key:'affinity',label:'親しさ（−1〜1）',type:'number',min:-1,max:1,required:true,value:relation.affinity},
        {key:'awareness',label:'認知（0〜1）',type:'number',min:0,max:1,required:true,value:relation.awareness}
      ];
      const add=this.button('＋ 関係を追加',()=>this.textDialog('人物間の関係を追加',[
        {key:'pair',label:'関係の向き（人物 → 相手）',type:'select',options:pairs,value:'',required:true},...fields({label:'',affinity:0,awareness:0})
      ],values=>{
        const [si,ti]=values.pair.split(':').map(Number),source=people[si],target=people[ti];
        if(!source||!target||source===target||!Array.isArray(source.relations)||source.relations.length>=7||source.relations.some(r=>r?.person===target.name))return 'この向きの関係は追加できません。';
        source.relations.push({person:target.name,label:values.label,affinity:values.affinity,awareness:values.awareness});
        refresh();focusAfter=add;
      },add,{focusAfter:()=>focusAfter}),!pairs.length);
      head.append(add);section.append(head);
      const hint=this.node('p','人物 → 相手の向きごとに設定します。逆方向の関係は別に編集してください。親しさと認知は開始時点の値です。');hint.className='ui-hint';section.append(hint,body);
      const refresh=()=>{
        this.controls=this.controls.filter(c=>!buttons.includes(c.node));buttons=[];body.replaceChildren();
        people.forEach(source=>{
          if(!Array.isArray(source.relations)){body.append(this.node('p',source.name+'の関係の形式は、詳しい修正欄で確認してください。'));return;}
          source.relations.forEach(relation=>{
            if(!relation||typeof relation.person!=='string'){body.append(this.node('p','関係の形式を詳しい修正欄で確認してください。'));return;}
            const row=this.node('div');row.className='wd-relation-row';
            const copy=this.node('div');copy.append(this.node('h4',source.name+' → '+relation.person));
            const detail=this.node('p');detail.className='wd-copy';
            const update=()=>{detail.textContent=(relation.label||'ラベルなし')+' ／ 親しさ '+relation.affinity+' ／ 認知 '+relation.awareness;};update();copy.append(detail);
            const actions=this.node('div');actions.className='wd-relation-actions';
            const edit=this.button('✎ 編集',()=>this.textDialog(source.name+' → '+relation.person+' の関係を編集',fields(relation),values=>{Object.assign(relation,values);update();},edit));
            edit.setAttribute('aria-label',source.name+'から'+relation.person+'への関係を編集');
            const remove=this.button('削除',()=>this.textDialog(source.name+' → '+relation.person+' の関係を削除',[],()=>{
              source.relations.splice(source.relations.indexOf(relation),1);refresh();focusAfter=add;
            },remove,{saveLabel:'削除する',note:'この向きの関係を下案から削除します。逆方向の関係は変わりません。採用する前に下案を再確認してください。',focusAfter:()=>focusAfter}));
            remove.setAttribute('aria-label',source.name+'から'+relation.person+'への関係を削除');
            buttons.push(edit,remove);actions.append(edit,remove);row.append(copy,actions);body.append(row);
          });
        });
        if(!body.children.length)body.append(this.node('p','関係は未設定です。「関係を追加」から設定できます。'));
        const available=people.some(source=>Array.isArray(source.relations)&&source.relations.length<7&&people.some(target=>source.name!==target.name&&!source.relations.some(r=>r?.person===target.name)));
        const lock=this.controls.find(c=>c.node===add);lock.locked=!available;add.disabled=this.disabled||lock.locked;
        pairs.splice(0);people.forEach((source,si)=>people.forEach((target,ti)=>{
          if(source.name!==target.name&&Array.isArray(source.relations)&&source.relations.length<7&&!source.relations.some(r=>r?.person===target.name))pairs.push([si+':'+ti,source.name+' → '+target.name]);
        }));
      };
      refresh();this.root.append(section);
    }
    render(draft){
      this.controls=[];this.root.replaceChildren();
      if(!draft||!Array.isArray(draft.people)||draft.people.length>8||!draft.people.length){this.root.hidden=true;return;}
      this.draft=JSON.parse(JSON.stringify(draft));this.root.hidden=false;
      this.root.append(this.node('h3','下案を修正する'));
      const hint=this.node('p','目標の文章と達成条件を揃えてください。条件をなくすと、自動判定なしの文章の目標になります。修正後は下の「下案を確認する」を押してください。');hint.className='ui-hint';this.root.append(hint);
      const names=key=>(Array.isArray(this.draft[key])?this.draft[key]:[]).filter(v=>v&&typeof v.name==='string').map(v=>[v.name,v.name]);
      const people=names('people'),places=names('places'),items=names('items'),facts=names('facts');
      const story=this.node('section');story.className='ui-card';
      const storyHead=this.node('div');storyHead.className='ui-section-head';storyHead.append(this.node('h3','初期物語'));
      const storyCopy=this.node('p',this.draft.initial_story||'未入力');storyCopy.className='wd-copy';
      const editStory=this.button('✎ 導入文を編集',()=>this.textDialog('導入文を編集',[
        {key:'initial_story',label:'文章',maxLength:8000,value:this.draft.initial_story}
      ],values=>{this.draft.initial_story=values.initial_story;storyCopy.textContent=values.initial_story||'未入力';},editStory));
      storyHead.append(editStory);story.append(storyHead,storyCopy);
      const storyHint=this.node('p','導入文を変更しても、人物の開始位置や関係の数値は変わりません。');storyHint.className='ui-hint';story.append(storyHint);this.root.append(story);
      const roles=this.node('section');roles.className='ui-card';roles.append(this.node('h3','主人公と相手役'));
      roles.append(this.field('主人公',this.select(people,this.draft.protagonist,name=>{this.draft.protagonist=name;this.publish(true);})));
      roles.append(this.field('相手役（友人・案内人も指定できます）',this.select(people.filter(p=>p[0]!==this.draft.protagonist),this.draft.antagonist,name=>{this.draft.antagonist=name;this.publish();})));
      this.root.append(roles);
      this.draft.people.forEach((person,index)=>{
        if(!person||typeof person.name!=='string')return;
        const card=this.node('section');card.className='wd-person ui-card';
        const head=this.node('div');head.className='ui-section-head';head.append(this.node('h3',person.name));
        const intro=this.node('p',typeof person.description==='string'&&person.description?person.description:'未入力');intro.className='wd-copy';
        const personality=this.node('p',typeof person.personality==='string'&&person.personality?person.personality:'未入力');personality.className='wd-copy';
        const edit=this.button('✎ 人物を編集',()=>this.textDialog(person.name+'を編集',[
          {key:'description',label:'人物の紹介',value:person.description},{key:'personality',label:'人物像',value:person.personality}
        ],values=>{Object.assign(person,values);intro.textContent=values.description||'未入力';personality.textContent=values.personality||'未入力';},edit));
        edit.setAttribute('aria-label',person.name+'の人物紹介を編集');head.append(edit);card.append(head,intro,this.node('h4','人物像'),personality,this.node('p','開始位置：'+(person.entry||'未設定')),this.node('h4','目標'));
        const value=goalValue(person.goal);
        if(!value){card.append(this.node('p','この目標の形式はJSON欄で確認してください。'));this.root.append(card);return;}
        const changeGoal=()=>{person.goal=value;this.publish();};
        const description=this.control(this.node('textarea'));description.rows=2;description.maxLength=2000;description.value=value.description;
        description.addEventListener('input',()=>{if(!description.disabled){value.description=description.value;changeGoal();}});
        card.append(this.field('目標の文章',description));
        if(!value.conditions.length){const note=this.node('p','自動判定なし。達成は内容を読んで確認します。');note.className='ui-hint';card.append(note);}
        value.conditions.forEach((condition,ci)=>{
          const row=this.node('section');row.className='ui-card';row.append(this.node('h3','達成条件 '+(ci+1)));
          const current=condition&&typeof condition==='object'?condition:{};
          const commit=()=>{person.goal=value;this.publish();};
          row.append(this.field('条件の種類',this.select(Object.entries(TYPES),current.type,type=>{
            value.conditions[ci]={type};
            if(type==='befriend')value.conditions[ci].threshold=0.5;
            person.goal=value;this.publish(true);
          })));
          const reference=(field,label,list)=>row.append(this.field(label,this.select(list,current[field],selected=>{current[field]=selected;commit();})));
          if(current.type==='reach'||current.type==='deliver')reference('place','到着する場所',places);
          if(current.type==='acquire'||current.type==='deliver')reference('item','品物',items);
          if(['meet','befriend','defeat'].includes(current.type))reference('person','相手',people.filter(p=>p[0]!==person.name));
          if(current.type==='discover')reference('fact','知る情報',facts);
          if(current.type==='befriend'||current.type==='survive'){
            const number=this.control(this.node('input'));number.type='number';
            const field=current.type==='befriend'?'threshold':'day';
            number.min=current.type==='befriend'?'0.01':'1';number.max=current.type==='befriend'?'1':'7';number.step=current.type==='befriend'?'0.05':'1';
            number.value=current[field]===undefined?'':String(current[field]);
            number.addEventListener('input',()=>{if(!number.disabled){current[field]=number.value===''?null:Number(number.value);commit();}});
            row.append(this.field(current.type==='befriend'?'双方の親しさ（0より大きく1以下）':'生存する日数（1〜7日）',number));
          }
          row.append(this.button('この条件を削除',()=>{value.conditions.splice(ci,1);person.goal=value;this.publish(true);}));
          card.append(row);
        });
        card.append(this.button('条件を追加',()=>{value.conditions.push({type:''});person.goal=value;this.publish(true);},value.conditions.length>=8));
        if(value.conditions.length)card.append(this.button('この目標は自動判定しない',()=>{value.conditions=[];person.goal=value;this.publish(true);}));
        this.root.append(card);
      });
      if(Array.isArray(this.draft.places)&&this.draft.places.length<=20){
        this.root.append(this.node('h3','場所'));
        this.draft.places.forEach(place=>{
          if(!place||typeof place.name!=='string')return;
          const section=this.node('section');section.className='wd-place ui-card';
          const head=this.node('div');head.className='ui-section-head';head.append(this.node('h3',place.name));
          const description=this.node('p',typeof place.description==='string'?place.description:'未入力');description.className='wd-copy';
          const edit=this.button('✎ 場所を編集',()=>this.textDialog(place.name+'を編集',[
            {key:'description',label:'場所の説明',value:place.description}
          ],values=>{place.description=values.description;description.textContent=values.description||'未入力';},edit));
          edit.setAttribute('aria-label',place.name+'の場所の説明を編集');head.append(edit);section.append(head,description);this.root.append(section);
        });
      }
      this.relationsEditor();
      this.itemsEditor();
      this.routesEditor();
    }
  }
  const api={Editor,goalValue};global.WorldBloomDraftGoals=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
