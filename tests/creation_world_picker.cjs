const assert=require('node:assert/strict');
const ui=require('../viewer/static/creation-world-picker.js');
class El{constructor(){this.value='';this.dataset={};this.events={};this.disabled=false;this.hidden=false;this.checked=false;}addEventListener(k,f){this.events[k]=f;}focus(){this.focused=true;}querySelector(s){return this.nodes[s];}}
function fixture(n,selected=''){
 const source=new El();source.value=selected;
 const search=new El(),count=new El(),empty=new El(),reset=new El(),picked=new El();
 const cards=Array.from({length:n},(_,i)=>{const c=new El();c.dataset={worldId:'world-'+i,worldName:i<2?'同じ名前':'長い名前'.repeat(30),search:'world-'+i+' 同じ名前 花子 ＡＢＣ '+i};const radio=new El();radio.value=c.dataset.worldId;c.nodes={'[data-cw-world-option]':radio,'[data-cw-world-label]':new El()};return c;});
 const host=new El();host.nodes={'[data-cw-source]':source,'[data-cw-world-search]':search,'[data-cw-world-count]':count,'[data-cw-world-empty]':empty,'[data-cw-world-reset]':reset};host.querySelectorAll=()=>cards;host.ownerDocument={querySelector:()=>picked};let changes=0;
 const picker=ui.mount(host,()=>changes++);return{source,search,count,empty,reset,picked,cards,picker,changes:()=>changes};
}
const f=fixture(120);
assert.equal(f.picker.selected(),'');assert.equal(f.count.textContent,'120件');
assert.equal(f.picker.choose('world-1'),true);assert.equal(f.picker.selected(),'world-1');assert.equal(f.cards[1].nodes['[data-cw-world-option]'].checked,true);assert.equal(f.cards[0].nodes['[data-cw-world-option]'].checked,false);
f.search.value='world-117';f.search.events.input();assert.equal(f.count.textContent,'1 / 120件');assert.equal(f.picker.selected(),'world-1');assert.match(f.picked.textContent,/検索結果の外/);
f.search.value='ａｂｃ';f.search.events.input();assert.equal(f.count.textContent,'120 / 120件');
f.search.value='not found';f.search.events.input();assert.equal(f.empty.hidden,false);f.reset.events.click();assert.equal(f.search.value,'');assert.equal(f.empty.hidden,true);assert.equal(f.search.focused,true);assert.equal(f.picker.selected(),'world-1');
f.source.disabled=true;assert.equal(f.picker.choose('world-2'),false);assert.equal(f.picker.selected(),'world-1');f.source.disabled=false;
f.cards[4].events.click({target:{closest:()=>null}});assert.equal(f.picker.selected(),'world-4');assert.equal(f.cards[4].nodes['[data-cw-world-option]'].focused,true);
f.cards[3].events.click({target:{closest:()=>({})}});assert.equal(f.picker.selected(),'world-4');
const r=f.cards[0].nodes['[data-cw-world-option]'];r.checked=true;r.events.change();assert.equal(f.picker.selected(),'world-0');assert.equal(f.changes(),3);
assert.equal(f.picker.choose('missing'),false);
const legacy=fixture(2,'world-1');assert.equal(legacy.picker.selected(),'world-1');assert.equal(legacy.cards[1].dataset.selected,'true');assert.equal(fixture(2,'missing').picker.selected(),'');assert.equal(fixture(0).count.textContent,'0件');
console.log('PASS source world cards: duplicate names use IDs, 120 rows, NFKC/person search, hidden choice retained, reset, invalid/legacy selection, radio change, card focus, busy guard, zero worlds.');
const fs=require('node:fs'),vm=require('node:vm');
const controller=fs.readFileSync(require.resolve('../viewer/static/creation-workflow.js'),'utf8');
const line=name=>controller.split('\n').find(x=>x.startsWith('async function '+name+'('));
const requests=[],loaded=[];let saves=0;
const context={S:{},allowed:true,isDirty:true,confirm:false,guard:()=>context.allowed,dirty:()=>context.isDirty,window:{confirm:()=>context.confirm},saveDraft:async()=>{saves++;return context.saveOK;},saveOK:false,uuid:()=> 'a'.repeat(32),run:async(_,fn)=>fn(),api:async(path,body)=>{requests.push({path,body});if(context.error)throw Error('offline');return{source:body.source};},loadNative:r=>loaded.push(r)};
vm.createContext(context);vm.runInContext(line('canSwitch')+'\n'+line('importSource'),context);
(async()=>{
 await context.importSource({world_id:'world-1'});assert.equal(requests.length,0);assert.equal(saves,0);
 context.confirm=true;await context.importSource({world_id:'world-1'});assert.equal(requests.length,0);assert.equal(saves,1);
 context.saveOK=true;await context.importSource({world_id:'world-1'});assert.equal(requests[0].body.source.world_id,'world-1');assert.equal(loaded[0].source.world_id,'world-1');
 context.allowed=false;await context.importSource({world_id:'world-2'});assert.equal(requests.length,1);
 context.allowed=true;context.isDirty=false;context.error=true;await assert.rejects(()=>context.importSource({world_id:'world-2'}));assert.equal(loaded.length,1);assert.equal(context.S.pending.action,'import');
 console.log('PASS actual import controller: declined switch, failed save, exact selected ID, blocked transition, uncertain response preserves pending receipt and does not load another world.');
})().catch(e=>{console.error(e);process.exitCode=1;});
