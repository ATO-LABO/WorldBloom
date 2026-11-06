// Test actual filtering and retained tab DOM, using the project's offline DOM.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const repo=process.argv[2]||path.resolve(__dirname,'..');
let fixture=fs.readFileSync(path.join(repo,'tests/people_settings_ui.cjs'),'utf8');fixture=fixture.slice(0,fixture.indexOf('function setup('));
const {Element,Event,setDocument}=vm.runInNewContext(fixture+'\n({Element,Event,setDocument(value){doc=value;}});',{require,process,console,URL,__dirname:path.join(repo,'tests')});
Element.prototype.insertBefore=function(node,before){node.remove?.();node.parentElement=this;this.nodes.splice(this.nodes.indexOf(before),0,node);};
const document={listeners:{},window:{listeners:{}},createElement:t=>new Element(t)};document.body=new Element('body');document.body._document=true;document.querySelector=s=>document.body.querySelector(s);setDocument(document);
const root=new Element('main');root.setAttribute('data-action-settings','');document.body.append(root);
const body=new Element('div');body.className='as-body';body.scrollTop=0;root.append(body);
const nav=new Element('nav');nav.setAttribute('role','tablist');root.append(nav);
const tabs={},panels={};for(const key of ['types','actions']){const t=new Element('button');t.dataset.asTab=key;nav.append(t);tabs[key]=t;const p=new Element('section');p.dataset.asPanel=key;body.append(p);panels[key]=p;}
const search=new Element('input');search.dataset.asSearch='';search.value='';panels.actions.append(search);
const genre=new Element('select');genre.dataset.asGenreFilter='';genre.value='all';panels.actions.append(genre);
const count=new Element('p');count.dataset.asCount='';const empty=new Element('p');empty.dataset.asEmpty='';panels.actions.append(count,empty);
const rows=[];for(const [key,text]of [['common','船を作る 木材 造船術'],['combat','相手と戦う 有効'],['romance','交際を始める 無効']]){const r=new Element('tr');r.dataset.asRow='';r.dataset.asGenre=key;r.dataset.asCategoryLabel=key;r.dataset.asDisabled=String(key==='romance');r.dataset.asSearchText=text;panels.actions.append(r);rows.push(r);}
const handlers={},location={hash:''},history={pushState(_a,_b,value){location.hash=value;}};
const context={document,location,history,URLSearchParams,window:{WorldBloomTabs:{enhance(){}}},addEventListener:(key,fn)=>handlers[key]=fn};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(repo,'viewer/static/action-settings.js'),'utf8'),context);
function event(target,type){target.dispatchEvent(new Event(type,{bubbles:true}));}
search.value='船';event(search,'input');assert.deepEqual(rows.map(r=>r.hidden),[false,true,true]);assert.equal(count.textContent,'1 / 3件');
event(tabs.types,'click');assert.equal(panels.types.hidden,false);event(tabs.actions,'click');assert.equal(search.value,'船');assert.equal(rows[0].hidden,false);
genre.value='romance';event(genre,'change');assert(rows.every(r=>r.hidden));assert.equal(empty.hidden,false);
search.value='';event(search,'input');assert.deepEqual(rows.map(r=>r.hidden),[true,true,false]);assert.equal(empty.hidden,true);
event(tabs.types,'click');event(tabs.actions,'click');assert.equal(genre.value,'romance');assert.equal(rows[2].hidden,false);
location.hash='#tab=people';handlers.popstate();assert.equal(tabs.actions.getAttribute('aria-selected'),'true');
assert.equal(document.querySelector('[data-as-search]'),search);assert.equal(root.querySelectorAll('[data-as-group]').length,1);assert.equal(root.querySelector('[data-as-group]').textContent,'romance（無効）');search.value='存在しない';event(search,'input');assert.equal(root.querySelectorAll('[data-as-group]').length,0);console.log('Action settings: combined genre/search, empty state, retained DOM/input/filter and URL tab state passed.');
