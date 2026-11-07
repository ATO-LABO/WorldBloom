(() => {
 'use strict';
 const empty=()=>({version:1,actors:[],max_depth:8,max_nodes:256});
 const definition=draft=>draft.actors.length?JSON.parse(JSON.stringify(draft)):null;
 function render(root,draft,snapshot,editable,change){
  root.replaceChildren();
  const node=(tag,text,parent=root)=>{const el=document.createElement(tag);el.textContent=text;parent.append(el);return el;};
  node('h2','目的の分解と再計画');
  node('p','人物が大切にする最終目的から、必要な準備と行動の順番を組み立てます。失敗や新しい情報を受けて手順を探し直します。');
  const group=node('fieldset','');node('legend','自分で計画を組み立てる人物',group);
  for(const person of snapshot.people){
   const label=node('label','',group);label.className='ux-inline';
   const input=document.createElement('input');input.type='checkbox';input.checked=draft.actors.includes(person.id);input.disabled=!editable;
   input.addEventListener('change',()=>{draft.actors=input.checked?[...draft.actors,person.id]:draft.actors.filter(id=>id!==person.id);change();});
   label.append(input,document.createTextNode(person.id));
  }
  node('p','人物を選ばない場合は、この機能を無効にします。中間目標は人物の価値観として追加されません。').className='ui-hint';
  for(const [key,labelText,min,max] of [['max_depth','一つの計画に含める行動数の上限',1,24],['max_nodes','一度に調べる候補数の上限',16,8192]]){
   const label=node('label','');label.className='ui-field';node('span',labelText,label);
   const input=document.createElement('input');input.type='number';input.min=String(min);input.max=String(max);input.step='1';input.required=true;input.value=String(draft[key]);input.disabled=!editable;
   input.addEventListener('input',()=>{draft[key]=Number(input.value);change();});label.append(input);
  }
  node('p','候補を探して見つからなくても、それだけで目的を失敗確定にはしません。本人の情報で計画し、達成・失敗・期限は別に判定します。').className='ui-notice';
  node('p','中間行動は「目的と対処の行動」、最終目的は人物の「大切にしていること」で定義します。現時点では、自分から始める行動を使う世界に対応します。').className='ui-hint';
 }
 window.WorldBloomPlanning={empty,definition,render};
})();
