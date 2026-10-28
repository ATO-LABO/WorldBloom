const assert=require('node:assert/strict');
const {Editor,goalValue}=require('../viewer/static/draft-goal-editor.js');
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.handlers={};this.disabled=false;this.hidden=false;this.value='';this.textContent='';this.attributes={};this.isConnected=true;}
  append(...nodes){this.children.push(...nodes);for(const n of nodes)n.parentNode=this;}
  replaceChildren(...nodes){for(const n of this.children)for(const child of all(n))child.isConnected=false;this.children=nodes;}
  setAttribute(key,value){this.attributes[key]=value;}
  getAttribute(key){return this.attributes[key];}
  addEventListener(type,fn){this.handlers[type]=fn;}
  emit(type){return this.handlers[type]?.({preventDefault(){}});}
  showModal(){this.open=true;}
  close(){this.open=false;this.emit('close');}
  remove(){this.parentNode.children=this.parentNode.children.filter(n=>n!==this);this.isConnected=false;}
  focus(){this.focused=true;}
  reportValidity(){return all(this).every(n=>!n.maxLength||n.value.length<=n.maxLength);}
  set innerHTML(_){throw Error('Unsafe HTML');}
}
const all=n=>[n,...n.children.flatMap(all)];
const root=new Node('div'),published=[];
const editor=new Editor(root,{createElement:tag=>new Node(tag)},d=>published.push(d));
const original={people:[{name:'<b>A</b>',description:'紹介',personality:'人物像',entry:'家',goal:{description:'Bと再会し、仲直りする',conditions:[{type:'befriend',person:'B',threshold:0.6}]}},{name:'B',goal:{description:'暮らす',conditions:[]}}],places:[{name:'家',description:'家の説明'},{name:'庭'}],items:[],facts:[]};
editor.render(original);
assert.equal(published.length,0);assert.match(all(root).map(n=>n.textContent).join(' '),/<b>A<\/b>/);
const editPerson=all(root).find(n=>n.attributes['aria-label']==='<b>A</b>の人物紹介を編集');
const controls=editor.controls.length;
editPerson.emit('click');let dialog=all(root).find(n=>n.tagName==='dialog');assert.ok(dialog.open);
let form=dialog.children[0],fields=all(dialog).filter(n=>n.tagName==='textarea');
fields[0].value='入力中';const beforeCancel=published.length;
all(dialog).find(n=>n.textContent==='キャンセル').emit('click');
assert.equal(published.length,beforeCancel);assert.equal(editor.draft.people[0].description,'紹介');assert.equal(editPerson.focused,true);assert.equal(editor.controls.length,controls);
editPerson.emit('click');dialog=all(root).find(n=>n.tagName==='dialog');form=dialog.children[0];fields=all(dialog).filter(n=>n.tagName==='textarea');
fields[0].value='<img src=x>\n新しい紹介';fields[1].value='新しい人物像';form.emit('submit');
assert.equal(published.at(-1).people[0].description,'<img src=x>\n新しい紹介');assert.equal(published.at(-1).people[0].personality,'新しい人物像');
assert.deepEqual(published.at(-1).people[0].goal,original.people[0].goal);assert.equal(original.people[0].description,'紹介');assert.equal(editor.controls.length,controls);
const editPlace=all(root).find(n=>n.attributes['aria-label']==='家の場所の説明を編集');editPlace.emit('click');
dialog=all(root).find(n=>n.tagName==='dialog');fields=all(dialog).filter(n=>n.tagName==='textarea');form=dialog.children[0];
fields[0].value='x'.repeat(2001);const beforeInvalid=published.length;form.emit('submit');assert.equal(published.length,beforeInvalid);assert.ok(dialog.open);
fields[0].value='改行を含む\n場所の説明';form.emit('submit');assert.equal(published.at(-1).places[0].description,fields[0].value);assert.equal(original.places[0].description,'家の説明');
assert.ok(all(root).some(n=>n.textContent==='<img src=x>\n新しい紹介'));
const counterpart=all(root).find(n=>n.tagName==='label'&&n.textContent.startsWith('相手役')).children[0];
counterpart.value='B';counterpart.emit('change');assert.equal(published.at(-1).antagonist,'B');
let description=all(root).find(n=>n.tagName==='textarea');description.value='Bと再会して仲直りする';description.emit('input');
assert.equal(published.at(-1).people[0].goal.description,description.value);assert.equal(original.people[0].goal.description,'Bと再会し、仲直りする');
let button=text=>all(root).find(n=>n.tagName==='button'&&n.textContent===text);
button('条件を追加').emit('click');
assert.deepEqual(published.at(-1).people[0].goal.conditions[1],{type:''});
let types=all(root).filter(n=>n.tagName==='select'&&n.children.some(o=>o.value==='meet'));
types[1].value='meet';types[1].emit('change');
let person=all(root).filter(n=>n.tagName==='select'&&n.children.some(o=>o.value==='B')&&!n.children.some(o=>o.value==='meet')).at(-1);
person.value='B';person.emit('change');
assert.deepEqual(published.at(-1).people[0].goal.conditions[1],{type:'meet',person:'B'});
editor.setDisabled(true);const count=published.length;description=all(root).find(n=>n.tagName==='textarea');description.value='ignored';description.emit('input');button('条件を追加').emit('click');assert.equal(published.length,count);
assert.ok(editor.controls.every(c=>c.node.disabled));editor.setDisabled(false);
button('この目標は自動判定しない').emit('click');assert.deepEqual(published.at(-1).people[0].goal.conditions,[]);
assert.match(all(root).map(n=>n.textContent).join(' '),/自動判定なし/);
editor.render({people:[{name:'A',goal:{target:'荷物',deliver_to:'庭'}}],places:[{name:'庭'}]});
assert.deepEqual(goalValue({target:'荷物',deliver_to:'庭'}).conditions,[{type:'deliver',item:'荷物',place:'庭'}]);
editor.render({people:[{name:'A',goal:{description:'不明な参照を保持',conditions:[{type:'reach',place:'未登録の場所'}]}}]});
assert.equal(all(root).find(n=>n.tagName==='select'&&n.value==='未登録の場所').value,'未登録の場所');
editor.render({people:[{name:'A',goal:null}]});assert.match(all(root).map(n=>n.textContent).join(' '),/JSON/);
const relationDraft={...original,initial_story:'元の導入',people:original.people.map(p=>({...p,relations:[]}))};
relationDraft.people[0].relations=[{person:'B',label:'知人',affinity:0.1,awareness:0.2}];
editor.render(relationDraft);
let findButton=label=>all(root).find(n=>n.attributes['aria-label']===label);
let open=()=>all(root).find(n=>n.tagName==='dialog'),values=()=>Object.fromEntries(all(open()).filter(n=>n.name).map(n=>[n.name,n]));
all(root).find(n=>n.textContent==='✎ 導入文を編集').emit('click');
values().initial_story.value='x'.repeat(8001);const beforeStory=published.length;open().children[0].emit('submit');assert.equal(published.length,beforeStory);
values().initial_story.value='<script>文章</script>\n'+ 'あ'.repeat(3000);open().children[0].emit('submit');
assert.equal(published.at(-1).initial_story,'<script>文章</script>\n'+'あ'.repeat(3000));assert.equal(relationDraft.initial_story,'元の導入');
const beforeRelation=published.length;findButton('<b>A</b>からBへの関係を編集').emit('click');
for(const invalid of ['NaN','Infinity','-1.1','1.1']){values().affinity.value=invalid;open().children[0].emit('submit');assert.equal(published.length,beforeRelation);assert.ok(open().open);}
values().affinity.value='-0.4';values().awareness.value='1.1';open().children[0].emit('submit');assert.equal(published.length,beforeRelation);
all(open()).find(n=>n.textContent==='キャンセル').emit('click');assert.equal(editor.draft.people[0].relations[0].affinity,0.1);
findButton('<b>A</b>からBへの関係を編集').emit('click');values().label.value='<b>幼なじみ</b>';values().affinity.value='-0.4';values().awareness.value='0.6';open().children[0].emit('submit');
assert.deepEqual(published.at(-1).people[0].relations[0],{person:'B',label:'<b>幼なじみ</b>',affinity:-0.4,awareness:0.6});
assert.deepEqual(published.at(-1).people[1].relations,[]);assert.deepEqual(relationDraft.people[0].relations,[{person:'B',label:'知人',affinity:0.1,awareness:0.2}]);
const addRelation=()=>all(root).find(n=>n.textContent==='＋ 関係を追加').emit('click');
addRelation();assert.deepEqual(values().pair.children.map(n=>n.value),['','1:0']);
const beforeAdd=published.length;open().children[0].emit('submit');assert.equal(published.length,beforeAdd);
values().pair.value='1:0';values().label.value='友人';values().affinity.value='0.3';values().awareness.value='0.8';open().children[0].emit('submit');
assert.deepEqual(published.at(-1).people[1].relations,[{person:'<b>A</b>',label:'友人',affinity:0.3,awareness:0.8}]);
assert.equal(all(root).find(n=>n.textContent==='＋ 関係を追加').disabled,true);
findButton('Bから<b>A</b>への関係を削除').emit('click');all(open()).find(n=>n.textContent==='キャンセル').emit('click');assert.equal(editor.draft.people[1].relations.length,1);
findButton('Bから<b>A</b>への関係を削除').emit('click');open().children[0].emit('submit');
assert.deepEqual(published.at(-1).people[1].relations,[]);assert.equal(editor.draft.people[0].relations[0].affinity,-0.4);
addRelation();assert.deepEqual(values().pair.children.map(n=>n.value),['','1:0']);open().close();
const cargo={...relationDraft,people:relationDraft.people.map(p=>({...p,goal:{description:'種を入手する',conditions:[]}})),items:[{name:'種',holder:'B'}],routes:[{from:'家',to:'庭',requires_item:'種',cost:2},{from:'庭',to:'家'}]};
cargo.people[0].goal.conditions=[{type:'acquire',item:'種'}];cargo.people[1].goal={target:'種',deliver_to:'庭'};
editor.render(cargo);
findButton('種を編集').emit('click');values().name.value='未保存';all(open()).find(n=>n.textContent==='キャンセル').emit('click');assert.equal(editor.draft.items[0].name,'種');
findButton('種を編集').emit('click');values().name.value='  <b>鍵</b>  ';values().holder.value='B';open().children[0].emit('submit');
assert.deepEqual(published.at(-1).items,[{name:'<b>鍵</b>',holder:'B'}]);
assert.equal(findButton('<b>鍵</b>を編集').focused,true);
assert.equal(published.at(-1).people[0].goal.conditions[0].item,'<b>鍵</b>');assert.equal(published.at(-1).people[1].goal.target,'<b>鍵</b>');assert.equal(published.at(-1).routes[0].requires_item,'<b>鍵</b>');assert.equal(published.at(-1).people[0].goal.description,'種を入手する');
assert.ok(all(root).some(n=>n.tagName==='select'&&n.value==='<b>鍵</b>'));assert.equal(cargo.items[0].name,'種');
findButton('<b>鍵</b>を削除').emit('click');const beforeUsedDelete=published.length;open().children[0].emit('submit');assert.equal(published.length,beforeUsedDelete);assert.match(all(open()).find(n=>n.attributes.role==='alert').textContent,/先に参照先/);open().close();
findButton('品物を追加').emit('click');const beforeItemAdd=published.length;values().name.value='<b>鍵</b>';values().holder.value='B';open().children[0].emit('submit');assert.equal(published.length,beforeItemAdd);assert.match(all(open()).find(n=>n.attributes.role==='alert').textContent,/同じ名前/);
values().name.value=' ';open().children[0].emit('submit');assert.equal(published.length,beforeItemAdd);
values().name.value='地図';values().holder.value='未登録';open().children[0].emit('submit');assert.equal(published.length,beforeItemAdd);
values().holder.value='<b>A</b>';open().children[0].emit('submit');assert.deepEqual(published.at(-1).items[1],{name:'地図',holder:'<b>A</b>'});
findButton('地図を削除').emit('click');open().close();assert.equal(editor.draft.items.length,2);
findButton('地図を削除').emit('click');open().children[0].emit('submit');assert.equal(editor.draft.items.length,1);
findButton('家 → 庭 の経路を編集').emit('click');assert.deepEqual(values().pair.children.map(n=>n.value),['','0:1']);assert.equal(values().cost.value,'2');
const beforeRoute=published.length;for(const invalid of ['0','-1','NaN','Infinity','10001','']){values().cost.value=invalid;open().children[0].emit('submit');assert.equal(published.length,beforeRoute);}
values().cost.value='2.5';values().requires_item.value='';open().children[0].emit('submit');assert.deepEqual(published.at(-1).routes[0],{from:'家',to:'庭',cost:2.5});assert.deepEqual(published.at(-1).routes[1],{from:'庭',to:'家'});
assert.equal(findButton('経路を追加').disabled,true);
findButton('家 → 庭 の経路を削除').emit('click');open().close();assert.equal(editor.draft.routes.length,2);
findButton('家 → 庭 の経路を削除').emit('click');open().children[0].emit('submit');assert.deepEqual(editor.draft.routes,[{from:'庭',to:'家'}]);
findButton('経路を追加').emit('click');assert.deepEqual(values().pair.children.map(n=>n.value),['','0:1']);
const beforeRouteAdd=published.length;values().pair.value='1:1';open().children[0].emit('submit');assert.equal(published.length,beforeRouteAdd);values().pair.value='1:0';open().children[0].emit('submit');assert.equal(published.length,beforeRouteAdd);
values().pair.value='0:1';values().requires_item.value='<b>鍵</b>';values().cost.value='10000';open().children[0].emit('submit');assert.deepEqual(published.at(-1).routes[1],{from:'家',to:'庭',requires_item:'<b>鍵</b>',cost:10000});
editor.render({...cargo,items:Array.from({length:8},(_,i)=>({name:'品'+i,holder:'B'}))});assert.equal(findButton('品物を追加').disabled,true);
editor.setDisabled(true);assert.ok(editor.controls.every(c=>c.node.disabled));
editor.render(null);assert.equal(root.hidden,true);
console.log('PASS text, story, directional relation editing/add/remove, bounds, goals, disabled and safe text');
