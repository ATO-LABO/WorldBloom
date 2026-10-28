// DOM/reader lifecycle simulation. This does not claim real-browser layout coverage.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.resolve(__dirname,'../viewer/static/purpose-workspace.js'),'utf8');
const receipt=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/melos-independent100-review-v1.json'),'utf8'));
const KEY='worldbloom-purpose-v1';
class El{
  constructor(){this.listeners={};this.children=[];this.dataset={};this.value='';this.hidden=false;this.disabled=false;this._text='';}
  addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
  fire(name){for(const fn of this.listeners[name]||[])fn({target:this});}
  append(el){this.children.push(el);}
  replaceChildren(){this.children=[];this._text='';}
  set textContent(text){this._text=text;this.children=[];}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
}
function environment({root=true,query='',storageError=false,initialSaved=null}={}){
  const fields={};for(const name of ['status','result','history','file','import','cancel','world','note','run','storage','copy','copy-status','copy-fallback'])fields[name]=new El();
  const select=new El(),label=new El(),notice=new El(),panels=['create','review'].map(mode=>{const e=new El();e.dataset.purposePanel=mode;return e;});
  const container=new El();container.querySelector=s=>fields[s.match(/data-purpose-(.*)\]/)[1]];
  const map=new Map(),events={},readers=[];let blocked=storageError;if(initialSaved)map.set(KEY,JSON.stringify(initialSaved));
  const context={URL,URLSearchParams,console,JSON,Set,Map,Object,Number,Error,
    location:{search:query,href:'http://localhost/modes'+query},
    history:{state:null,replaceState(_s,_t,url){context.location.href='http://localhost'+url;context.location.search='';}},
    sessionStorage:{getItem(k){if(blocked)throw Error('blocked');return map.get(k)||null;},setItem(k,v){if(blocked)throw Error('quota');map.set(k,v);}},
    document:{documentElement:{dataset:{}},querySelector:s=>root?container:null,
      querySelectorAll:s=>s==='[data-purpose]'?[select]:s==='[data-purpose-label]'?[label]:s==='[data-purpose-notice]'?[notice]:s==='[data-purpose-panel]'?panels:[],
      createElement:()=>new El()},
    addEventListener(name,fn){(events[name]||=[]).push(fn);},
    FileReader:class {constructor(){readers.push(this);}readAsText(file){if(file.throw)throw Error('read');}abort(){this.aborted=true;}},
    fetch(){throw Error('This UI must not call a job API');}};
  context.window=context;vm.runInNewContext(source,context);
  return {fields,select,label,notice,panels,context,map,readers,block(v){blocked=v;},fire(name,event={}){for(const fn of events[name]||[])fn(event);},
    load(index,value=receipt){const r=readers[index];r.result=typeof value==='string'?value:JSON.stringify(value);return r.onload();},
    stored(){return JSON.parse(map.get(KEY));}};
}
const e=environment({query:'?mode=review'}),f=e.fields;
assert.equal(e.label.textContent,'既存物語レビュー');assert.equal(e.context.location.search,'?mode=review');
assert.equal(e.panels[0].hidden,true);assert.equal(e.panels[1].hidden,false);
f.note.value='共通メモ';f.note.fire('input');f.world.value='melos';f.world.fire('change');
f.import.fire('click');assert.match(f.status.textContent,/選んで/);
f.file.files=[{size:e.context.WorldBloomPurpose.MAX_BYTES+1}];f.import.fire('click');assert.equal(e.readers.length,0);
f.file.files=[{size:e.context.WorldBloomPurpose.MAX_BYTES}];f.import.fire('click');f.import.fire('click');assert.equal(e.readers.length,1);
assert.equal(f.cancel.disabled,false);e.select.value='create';e.select.fire('change');e.load(0);
assert.equal(e.context.document.documentElement.dataset.purposeMode,'review');assert.equal(e.label.textContent,'既存物語レビュー');assert.match(f.result.textContent,/100件/);assert.equal(f.note.value,'共通メモ');
f.import.fire('click');f.cancel.fire('click');e.load(1,{...receipt,id:'cancelled'});
assert.equal(e.stored().history.length,1);assert.equal(e.readers[1].aborted,true);
f.import.fire('click');e.load(2,'bad json');assert.match(f.status.textContent,/過去結果/);assert.equal(e.stored().history.length,1);
f.import.fire('click');e.readers[3].onerror();assert.equal(f.import.disabled,false);
f.file.files=[{size:100,throw:true}];f.import.fire('click');assert.equal(f.import.disabled,false);assert.equal(f.cancel.disabled,true);
f.file.files=[{size:100}];f.import.fire('click');e.fire('pagehide');e.load(5,{...receipt,id:'late'});e.fire('pageshow',{persisted:true});
assert.equal(e.stored().history.length,1);assert.match(f.status.textContent,/離れたため/);
const fresh=e.stored();fresh.mode='create';fresh.history.push({...receipt,id:'other-page'});e.map.set(KEY,JSON.stringify(fresh));
e.fire('pageshow',{persisted:true});assert.equal(e.label.textContent,'既存物語レビュー');assert.equal(e.context.document.documentElement.dataset.purposeMode,'review');assert.equal(f.history.children.length,2);assert.equal(f.note.value,'共通メモ');
e.block(true);f.note.value='容量不足でも保持';f.note.fire('input');assert.match(f.storage.textContent,/保存が使えません/);
e.select.value='create';e.select.fire('change');e.select.value='review';e.select.fire('change');assert.equal(f.note.value,'容量不足でも保持');assert.match(f.result.textContent,/100件/);
const other=environment({root:false});other.map.set(KEY,JSON.stringify({...fresh,history:[receipt]}));other.select.value='review';other.select.fire('change');
assert.equal(other.stored().history.length,1);other.map.set(KEY,JSON.stringify({...fresh,mode:'review'}));other.fire('pageshow',{persisted:true});assert.equal(other.label.textContent,'物語制作');
const blocked=environment({storageError:true});assert.match(blocked.fields.storage.textContent,/保存が使えません/);
console.log('Purpose DOM checks passed: read-only purpose, stable purpose during load/BFCache, click burst, cancel/late result, malformed/error, storage failure, no backend calls.');
module.exports={environment};
