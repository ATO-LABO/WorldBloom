// Shared offline DOM and world-settings fixture.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const repo=process.argv[2]||path.resolve(__dirname,'..');
const decode=s=>String(s).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const camel=s=>s.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
let focused=null,doc;
class Event{
 constructor(type,values={}){Object.assign(this,{type,bubbles:true,cancelable:true,defaultPrevented:false},values);}
 preventDefault(){this.defaultPrevented=true;}
 stopPropagation(){this.stopped=true;}
 stopImmediatePropagation(){this.immediate=true;this.stopped=true;}
}
class TextNode{constructor(value){this.nodeType=3;this.textContent=value;this.parentElement=null;}get parentNode(){return this.parentElement;}}
function relationSelector(s){let quote='',bracket=0;for(let i=s.length-1;i>=0;i--){const c=s[i];if(quote){if(c===quote)quote='';continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c===']')bracket++;else if(c==='[')bracket--;else if(!bracket&&c==='>')return [s.slice(0,i).trim(),s.slice(i+1).trim(),true];else if(!bracket&&c===' ')return [s.slice(0,i).trim(),s.slice(i+1).trim(),false];}return null;}
class Element{
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.nodeType=1;this.attrs={};this.dataset={};this.nodes=[];this.listeners={};this.style={};this.scrollTop=0;this.scrollLeft=0;this.open=false;}
 get children(){return this.nodes.filter(n=>n instanceof Element);}get childNodes(){return this.nodes;}get parentNode(){return this.parentElement;}
 get isConnected(){let p=this;while(p){if(p._document)return true;p=p.parentElement;}return false;}
 get className(){return this.attrs.class||'';}set className(v){this.attrs.class=String(v);}
 get classList(){return {contains:name=>this.className.split(/\s+/).includes(name),add:(...names)=>this.className=[...new Set([...this.className.split(/\s+/).filter(Boolean),...names])].join(' '),remove:(...names)=>this.className=this.className.split(/\s+/).filter(n=>!names.includes(n)).join(' '),toggle:(name,on)=>{const found=this.className.split(/\s+/).includes(name),next=on===undefined?!found:on;this.className=next?[...new Set([...this.className.split(/\s+/).filter(Boolean),name])].join(' '):this.className.split(/\s+/).filter(n=>n!==name).join(' ');return next;}};}
 get id(){return this.attrs.id||'';}set id(v){this.attrs.id=String(v);}
 get value(){if(this._value!==undefined)return this._value;if(this.tagName==='SELECT')return (this.children.find(n=>n.hasAttribute('selected'))||this.children[0])?.value||'';return this.attrs.value??(this.tagName==='TEXTAREA'?this.textContent:'');}set value(v){this._value=String(v??'');}
 get selectedOptions(){return this.children.filter(n=>n.value===this.value);}get name(){return this.attrs.name||'';}
 get type(){return this.attrs.type||(this.tagName==='BUTTON'?'submit':'text');}set type(v){this.attrs.type=String(v);}
 get required(){return this.hasAttribute('required');}set required(v){v?this.attrs.required='':delete this.attrs.required;}
 get disabled(){return this.hasAttribute('disabled');}set disabled(v){v?this.attrs.disabled='':delete this.attrs.disabled;}
 get hidden(){return this.hasAttribute('hidden');}set hidden(v){v?this.attrs.hidden='':delete this.attrs.hidden;}
 get href(){return this.attrs.href?new URL(this.attrs.href,doc.location.href).href:'';}set href(v){this.attrs.href=String(v);}
 get textContent(){return this.nodes.map(n=>n.textContent||'').join('');}set textContent(v){this.replaceChildren(new TextNode(String(v)));}
 get innerHTML(){return this._html||'';}set innerHTML(html){this._html=String(html);this.replaceChildren();parseHTML(this._html,this);}
 append(...values){for(let n of values){if(typeof n!=='object')n=new TextNode(String(n));n.remove?.();if(n.parentElement)n.parentElement.nodes.splice(n.parentElement.nodes.indexOf(n),1);n.parentElement=this;this.nodes.push(n);}}
 prepend(...values){const old=[...this.nodes];this.replaceChildren(...values,...old);}
 replaceChildren(...values){for(const n of this.nodes)n.parentElement=null;this.nodes=[];this.append(...values);}
 remove(){if(this.parentElement){const p=this.parentElement;this.parentElement=null;p.nodes.splice(p.nodes.indexOf(this),1);}}
 setAttribute(k,v){this.attrs[k]=String(v);if(k.startsWith('data-'))this.dataset[camel(k.slice(5))]=String(v);}
 getAttribute(k){return k.startsWith('data-')?this.dataset[camel(k.slice(5))]??null:this.attrs[k]??null;}
 hasAttribute(k){return this.getAttribute(k)!==null;}removeAttribute(k){delete this.attrs[k];if(k.startsWith('data-'))delete this.dataset[camel(k.slice(5))];}
 matches(selector){
  if(selector.includes(','))return selector.split(',').some(s=>this.matches(s.trim()));
  const relation=relationSelector(selector);if(relation){const[left,right,direct]=relation;if(!this.matches(right))return false;if(direct)return !!this.parentElement?.matches(left);for(let p=this.parentElement;p;p=p.parentElement)if(p.matches(left))return true;return false;}
  let s=selector;for(const m of [...s.matchAll(/\[([^\]\s=^]+)(?:(\^?=)["']?([^\]"']*)["']?)?\]/g)]){const value=this.getAttribute(m[1]);if(value===null||m[2]&&(m[2]==='^='?!value.startsWith(m[3]):value!==m[3]))return false;}s=s.replace(/\[[^\]]+\]/g,'');
  for(const m of s.matchAll(/\.([\w-]+)/g))if(!this.classList.contains(m[1]))return false;s=s.replace(/\.[\w-]+/g,'');
  const id=s.match(/#([\w-]+)/);if(id&&this.id!==id[1])return false;s=s.replace(/#[\w-]+/g,'');return !s||s==='*'||this.tagName===s.toUpperCase();
 }
 querySelectorAll(selector){if(selector.startsWith(':scope>'))return this.children.filter(n=>n.matches(selector.slice(7)));const found=[];const walk=node=>{for(const child of node.children){if(child.matches(selector))found.push(child);walk(child);}};walk(this);return found;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
 addEventListener(type,fn,capture=false){(this.listeners[type]??=[]).push({fn,capture:!!capture});}
 dispatchEvent(event){event.target??=this;const ancestors=[];for(let p=this.parentElement;p;p=p.parentElement)ancestors.push(p);const chain=[doc.window,doc,...ancestors.reverse(),this];for(const node of chain){for(const entry of node.listeners?.[event.type]||[])if(entry.capture){entry.fn(event);if(event.immediate)return !event.defaultPrevented;}if(event.stopped)return !event.defaultPrevented;}
  for(const node of [...chain].reverse()){for(const entry of node.listeners?.[event.type]||[])if(!entry.capture){entry.fn(event);if(event.immediate)return !event.defaultPrevented;}if(node['on'+event.type])node['on'+event.type](event);if(event.stopped)break;}return !event.defaultPrevented;
 }
 click(){if(this.disabled)return;const event=new Event('click');this.dispatchEvent(event);if(!event.defaultPrevented&&this.tagName==='BUTTON'&&this.type==='submit')this.closest('form')?.dispatchEvent(new Event('submit'));}
 focus(){focused=this;doc.activeElement=this;}scrollIntoView(){this.scrolled=true;}
 getBoundingClientRect(){return {left:0,right:600,width:this.isConnected?600:0};}
 get viewBox(){return {baseVal:{width:1000,height:600}};}
 showModal(){this.open=true;}close(){this.open=false;this.dispatchEvent(new Event('close'));}
 reportValidity(){this.reported=true;return !this.required||this.value!=='';}
}
function parseHTML(html,parent){const stack=[parent],voids=new Set(['INPUT','BR','HR','IMG','META','LINK','AREA','BASE','EMBED','PARAM','SOURCE','TRACK','WBR']);for(const match of html.matchAll(/<!--[\s\S]*?-->|<\/?[^>]+>|[^<]+/g)){const token=match[0];if(token.startsWith('<!--'))continue;if(token.startsWith('</')){stack.pop();continue;}if(token.startsWith('<')){const opening=token.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/);if(!opening)continue;const node=new Element(opening[1]);for(const attr of opening[2].matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))node.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));stack[stack.length-1].append(node);if(!voids.has(node.tagName)&&!token.endsWith('/>'))stack.push(node);}else stack[stack.length-1].append(new TextNode(decode(token)));}}
function setup(hash='#screen=people',options={}){
 let confirmation=false,failure=null,hold=null;const requests=[],handlers={};focused=null;
 const current={id:'fixture',name:'fixture',editable:options.editable!==false,revision:'r1',configUrl:'/jobs/new?world=fixture',genre:null,genres:[],world:{name:'fixture',protagonist:'a',antagonist:'c',target_ending:['finish'],ending:[{id:'finish',label:'finish'}],zones:['town'],routes:{town:[]},time:{days:2,slots:['morning','night']},stamina:{default_max:10},goal_rules:{definitions:[]}},people:[{id:'a',description:'description-a',personality:'kind',range:{entry:'town'},inventory:{coin:10},stamina:{max:10,current:3},relations:{b:{affinity:.5,awareness:1,label:'friends'}}},{id:'b',description:'description-b',personality:'bold',range:{entry:'town'},inventory:{coin:70},stamina:{max:20,current:17},relations:{a:{affinity:-.3,awareness:1,label:'rivals'}}},{id:'c',description:'description-c',personality:'quiet',range:{entry:'town'},inventory:{coin:5},stamina:{max:30,current:25},relations:{a:{affinity:.2,awareness:1,label:'known'}}}],story_genre_features:[],stamina_action_catalog:[]};
 const location={hash,search:options.search||'',pathname:'/worlds/fixture',_href:''};Object.defineProperty(location,'href',{get(){return this._href||'http://localhost'+this.pathname+this.search+this.hash;},set(value){this._href=String(value);}});
 const window={listeners:{},confirm:()=>confirmation,addEventListener(type,fn,capture=false){(this.listeners[type]??=[]).push({fn,capture});}};
 doc={listeners:{},window,location,activeElement:null,readyState:'complete',createElement:t=>new Element(t),createTextNode:t=>new TextNode(t),addEventListener(type,fn,capture=false){(this.listeners[type]??=[]).push({fn,capture});}};doc.body=new Element('body');doc.body._document=true;doc.querySelector=s=>doc.body.querySelector(s);doc.querySelectorAll=s=>doc.body.querySelectorAll(s);doc.getElementById=id=>doc.body.querySelector('#'+id);
 const root=new Element('main');root.setAttribute('data-world-prototype','');const content=new Element('section');content.id='wp-content';content.className='wp-content';const data=new Element('script');data.id='wp-data';data.textContent=JSON.stringify(current);
 const nav=new Element('nav');nav.className='wp-nav';for(const screen of ['overview','description','people','time','checks']){const button=new Element('button');button.type='button';button.setAttribute('data-screen',screen);button.setAttribute('data-settings-category',screen);nav.append(button);}
 const dialog=new Element('dialog');dialog.innerHTML='<h2 id="wp-dialog-title"></h2><p id="wp-dialog-desc"></p><form id="wp-form"><div id="wp-fields"></div><button type="button" data-close>close</button><button type="submit" id="wp-dialog-submit">save</button></form>';
 const message=new Element('p');message.id='wp-message';const next=new Element('a');next.setAttribute('data-next','');const picker=new Element('select');picker.setAttribute('data-wb','world-picker');picker.value='fixture';doc.body.append(picker,root);root.append(data,nav,content,dialog,message,next);
 const history={entries:[hash],index:0,pushState(a,b,value){this.entries.splice(this.index+1);this.entries.push(value);this.index++;location.hash=value;},replaceState(a,b,value){this.entries[this.index]=value;location.hash=value;},back(){if(this.index){this.index--;location.hash=this.entries[this.index];emitWindow('popstate');}}};
 function emitWindow(type,event=new Event(type)){for(const entry of [...window.listeners[type]||[]])entry.fn(event);return event;}
 const context={console,structuredClone,URLSearchParams,URL,document:doc,location,history,window,CSS:{escape:v=>v},Event,MouseEvent:Event,confirm:()=>confirmation,addEventListener:window.addEventListener.bind(window),getComputedStyle:element=>({display:element.hidden?'none':'block'}),MutationObserver:class{observe(){}},requestAnimationFrame:fn=>{fn();return 1;},cancelAnimationFrame(){},WorldTimeSettings:class{guard(fn){fn();}leave(){}mount(){}refreshWorld(){}},fetch:async(url,request)=>{const body=JSON.parse(request.body);requests.push(structuredClone(body));if(hold)await new Promise(resolve=>hold=resolve);if(failure==='network')throw Error('fixture disconnected');if(failure==='malformed')return {ok:true,status:200,json:async()=>({world:{}})};if(failure)return {ok:false,status:failure,json:async()=>({message:'fixture rejected'})};assert.equal(body.operation,'roles');assert.equal(body.revision,current.revision);assert.equal(body.target,null);assert.deepEqual(Object.keys(body.values).sort(),['antagonist','protagonist','target_ending']);assert.equal(body.values.antagonist,'c');assert.deepEqual(Array.from(body.values.target_ending),['finish']);current.world.protagonist=body.values.protagonist;current.revision='r'+(Number(current.revision.slice(1))+1);return {ok:true,status:200,json:async()=>structuredClone(current)};}};
 window.WorldBloomPersonValues={effective:()=>[]};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(repo,'viewer/static/world-prototype.js'),'utf8'),context);vm.runInContext(fs.readFileSync(path.join(repo,'viewer/static/ui-tabs.js'),'utf8'),context);
 return {root,content,dialog,current,requests,history,location,document:doc,picker,context,get focused(){return focused;},confirm:value=>confirmation=value,fail:value=>failure=value,pause:()=>hold=true,release:()=>{hold();hold=null;},jump(value){location.hash=value;emitWindow('popstate');},unload(){return emitWindow('beforeunload');},field(){return content.querySelector('[data-protagonist-select]');},tab(id){return content.querySelector('[data-view="people:'+id+'"]');},choose(id){this.tab(id).click();},change(value){const field=this.field();field.value=value;field.onchange();},submit(){return content.querySelector('[data-protagonist-form]').onsubmit(new Event('submit'));},key(key){const button=content.querySelector('[data-view][aria-selected="true"]');button.dispatchEvent(new Event('keydown',{key}));},clickScreen(screen){const button=nav.querySelector('[data-screen="'+screen+'"]');const event=new Event('click');button.dispatchEvent(event);return event;}};
}
const active=(t,id)=>{assert.equal(t.tab(id).getAttribute('aria-selected'),'true');assert.equal(t.content.querySelector('[data-people-body]').getAttribute('aria-labelledby'),'wp-people-tab-'+id);};
