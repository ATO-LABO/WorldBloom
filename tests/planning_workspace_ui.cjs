const assert = require('node:assert/strict');
const {init} = require('../viewer/static/planning-workspace.js');
class Element {
 constructor(dataset={},attrs={}){this.dataset=dataset;this.attrs=attrs;this.listeners={};this.hidden=false;}
 addEventListener(name,fn){this.listeners[name]=fn;}
 getAttribute(name){return this.attrs[name]??null;}
 setAttribute(name,value){this.attrs[name]=value;}
 hasAttribute(name){return name==='data-plan-routine'&&this.routine;}
 focus(){focused=this;}
 fire(name,extra={}){let prevented=false;this.listeners[name]({...extra,preventDefault(){prevented=true;}});return prevented;}
}
let focused;
function group(index){
 const g=new Element({planGroupPanel:String(index)});
 g.buttons=[0,1,2].map(i=>new Element({planEvent:String(i)},{'aria-pressed':String(i===2)}));
 g.buttons[1].routine=true;g.buttons[1].hidden=true;
 g.panels=[0,1,2].map(i=>new Element({planDetail:String(i)}));g.panels.forEach((p,i)=>p.hidden=i!==2);
 g.all=new Element({}, {'aria-pressed':'false'});
 g.track={clientWidth:100,scrollWidth:300,scrollLeft:0,getBoundingClientRect:()=>({left:0,right:100})};
 g.buttons.forEach((b,i)=>b.getBoundingClientRect=()=>({left:i*100-g.track.scrollLeft,right:(i+1)*100-g.track.scrollLeft,width:100}));
 g.querySelectorAll=s=>s==='[data-plan-event]'?g.buttons:g.panels;
 g.querySelector=s=>s==='[data-plan-all]'?g.all:s==='.pw-events'?g.track:g.panels.find(p=>s===`[data-plan-detail="${p.dataset.planDetail}"]`);
 return g;
}
const groups=[group(0),group(1)],picker=new Element();picker.value='0';
const root=new Element();root.querySelector=()=>picker;root.querySelectorAll=()=>groups;
init(root);
assert.equal(root.dataset.planningReady,'true');
groups[0].buttons[0].fire('click');
assert.equal(groups[0].panels[0].hidden,false);assert.equal(groups[0].panels[2].hidden,true);
assert.equal(groups[0].buttons[0].fire('keydown',{key:'ArrowDown'}),true);
assert.equal(focused,groups[0].buttons[2]); // hidden routine is skipped
assert.equal(groups[0].track.scrollLeft,200); // reveal the selection inside the local timeline
groups[0].all.fire('click');assert.equal(groups[0].buttons[1].hidden,false);
groups[0].buttons[1].fire('click');assert.equal(groups[0].panels[1].hidden,false);
groups[0].all.fire('click');assert.equal(groups[0].buttons[1].hidden,true);
assert.equal(groups[0].panels[1].hidden,true);assert.equal(groups[0].panels[0].hidden,false);
picker.value='1';picker.fire('change');assert.equal(groups[0].hidden,true);assert.equal(groups[1].hidden,false);
groups[1].buttons[2].fire('keydown',{key:'Home'});assert.equal(focused,groups[1].buttons[0]);
groups[1].buttons[0].fire('keydown',{key:'End'});assert.equal(focused,groups[1].buttons[2]);
picker.value='0';picker.fire('change');assert.equal(groups[0].panels[0].hidden,false); // independent selection is preserved
assert.equal(groups[0].all.getAttribute('aria-pressed'),'false');
console.log('Planning workspace: historical selection, grouped state, expansion, hidden selection and keyboard passed.');
