(function(){
/* Pure, directed relationship model shared by the graph and matrix. */
const WorldRelations = (() => {
  const validObject = v => v && typeof v === 'object' && !Array.isArray(v);
  const compare = (a,b) => a < b ? -1 : a > b ? 1 : 0;
  const affiliation = p => (typeof p.affiliation === 'string' && p.affiliation.trim()) || p.range?.entry || 'その他';
  function buildRelationGraph(people) {
    const counts = new Map();
    people.forEach(p => { if(typeof p.id === 'string' && p.id) counts.set(p.id,(counts.get(p.id)||0)+1); });
    const nodes = people.filter(p => counts.get(p.id)===1).map(p => ({id:p.id,group:affiliation(p),person:p}));
    const ids = new Set(nodes.map(n=>n.id)), edges=[], issues=[];
    for(const [id,count] of counts) if(count>1) issues.push(`${id}: 人物名が重複しています`);
    for(const n of nodes) {
      if(n.person.relations != null && !validObject(n.person.relations)) { issues.push(`${n.id}: 関係の形式を確認してください`); continue; }
      for(const [target,relation] of Object.entries(n.person.relations||{})) {
        if(!validObject(relation)) { issues.push(`${n.id} → ${target}: 関係の形式を確認してください`); continue; }
        if(!ids.has(target)) { issues.push(`${n.id} → ${target}: 相手が未登録または一意ではありません`); continue; }
        if(n.id===target) continue;
        edges.push({...relation,source:n.id,target});
      }
    }
    edges.sort((a,b)=>compare(a.source,b.source)||compare(a.target,b.target));
    return {nodes,edges,issues};
  }
  function buildVisualEdges(graph) {
    const pairs=new Map();
    for(const edge of graph.edges) {
      const [a,b]=[edge.source,edge.target].sort(compare), key=JSON.stringify([a,b]);
      if(!pairs.has(key)) pairs.set(key,{a,b,directions:[]});
      pairs.get(key).directions.push(edge);
    }
    return [...pairs.values()];
  }
  function filterRelationGraph(graph,{mode='all',focusId='',scope='all',group='',kind='all'}={}) {
    const eligible=graph.edges.filter(e=>kind==='all'||(kind==='positive'?Number(e.affinity)>0:kind==='negative'?Number(e.affinity)<0:Number(e.affinity)===0));
    const reachable=new Set([focusId]);
    if(mode==='focus' && scope!=='all') {
      const depth=scope==='direct'?1:2;
      for(let d=0;d<depth;d++) {
        const prev=new Set(reachable);
        eligible.forEach(e=>{if(prev.has(e.source))reachable.add(e.target);if(prev.has(e.target))reachable.add(e.source);});
      }
    }
    const nodes=graph.nodes.filter(n=>(mode!=='focus'||scope==='all'||reachable.has(n.id))&&(!group||n.group===group||n.id===focusId));
    const ids=new Set(nodes.map(n=>n.id));
    return {nodes,edges:eligible.filter(e=>ids.has(e.source)&&ids.has(e.target)),issues:graph.issues};
  }
  function layoutRelationGraph(graph,anchorId="") {
    const anchorGroup=graph.nodes.find(n=>n.id===anchorId)?.group;
    const groups=[...new Set(graph.nodes.map(n=>n.group))].sort((a,b)=>Number(b===anchorGroup)-Number(a===anchorGroup)||compare(a,b));
    const columns=Math.min(3,Math.max(1,groups.length)), positioned=[], boxes=[];
    let y=32, width=columns*350+24;
    for(let row=0;row<groups.length;row+=columns) {
      const batch=groups.slice(row,row+columns).map(group=>({group,nodes:graph.nodes.filter(n=>n.group===group).sort((a,b)=>compare(a.id,b.id))}));
      const heights=batch.map(g=>Math.max(330,Math.ceil(g.nodes.length/3)*112+94)), height=Math.max(...heights);
      batch.forEach((g,i)=>{
        const anchor=g.nodes.findIndex(n=>n.id===anchorId);
        if(anchor>=0){const cols=Math.min(3,g.nodes.length),rows=Math.ceil(g.nodes.length/cols),center=Math.min(g.nodes.length-1,Math.floor((rows-1)/2)*cols+Math.floor(cols/2));[g.nodes[anchor],g.nodes[center]]=[g.nodes[center],g.nodes[anchor]];}
        const x=24+i*350, h=heights[i];
        boxes.push({name:g.group,x,y,width:326,height:h,index:groups.indexOf(g.group)});
        g.nodes.forEach((n,j)=>{
          const cols=Math.min(3,g.nodes.length), rows=Math.ceil(g.nodes.length/cols);
          const nodeRow=Math.floor(j/cols), nodeCol=j%cols, lastCount=Math.min(cols,g.nodes.length-nodeRow*cols);
          let nx=x+163+(nodeCol-(lastCount-1)/2)*101,ny=y+94+(nodeRow+(rows===1?0.5:0))*112;
          // Small groups form a triangle/ring rather than a single ruler row.
          if(g.nodes.length===1){nx=x+163;ny=y+190;}
          else if(g.nodes.length===2){nx=x+88+j*150;ny=y+175;}
          else if(g.nodes.length===3){const points=[[86,123],[240,123],[163,259]];nx=x+points[j][0];ny=y+points[j][1];}
          positioned.push({...n,x:nx,y:ny,groupIndex:groups.indexOf(g.group)});
        });
      });
      y+=height+24;
    }
    return {nodes:positioned,groups:boxes,width,height:Math.max(390,y)};
  }
  return {affiliation,buildRelationGraph,buildVisualEdges,filterRelationGraph,layoutRelationGraph};
})();


const NS='http://www.w3.org/2000/svg';
const html=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el};
const svg=(tag,attrs={},text)=>{const el=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));if(text!==undefined)el.textContent=text;return el};
let serial=0;

// The draft adapter feeds the unchanged world-settings relation model.
function draftPeopleGraph(draft){
  return WorldRelations.buildRelationGraph(draft.people.map(p=>({
    ...p,id:p.name,range:{entry:p.entry},relations:Object.fromEntries((p.relations||[]).map(r=>[r.person,{...r}]))
  })));
}

function placeGraph(draft){
  const counts=new Map();
  draft.places.forEach(p=>counts.set(p.name,(counts.get(p.name)||0)+1));
  const nodes=draft.places.filter(p=>counts.get(p.name)===1).map(p=>({id:p.name,group:'場所',person:p}));
  const ids=new Set(nodes.map(p=>p.id)),edges=[],issues=[];
  for(const [name,count] of counts)if(count>1)issues.push(`${name}：場所名が重複しています`);
  draft.routes.forEach((r,index)=>{
    if(!ids.has(r.from)||!ids.has(r.to)){issues.push(`${r.from} → ${r.to}：経路の場所を確認してください`);return}
    edges.push({...r,source:r.from,target:r.to,label:r.requires_item?`${r.requires_item}が必要`:'移動',index});
  });
  return {nodes,edges,issues};
}

function placeLayout(graph){
  // Stable topological view: coordinates are presentation only, never map distances.
  const cols=Math.min(3,Math.max(1,Math.ceil(Math.sqrt(graph.nodes.length))));
  const rows=Math.max(1,Math.ceil(graph.nodes.length/cols)),width=Math.max(460,cols*230+80),height=Math.max(340,rows*180+100);
  return {width,height,groups:[],nodes:graph.nodes.map((n,i)=>({...n,x:cols===1?width/2:110+(i%cols)*(width-220)/(cols-1),y:rows===1?height/2:110+Math.floor(i/cols)*(height-220)/(rows-1),groupIndex:0}))};
}

function diagram({graph,selected,kind='people',onNode,onEdge,anchor}){
  const root=html('section','draft-diagram wp-rel-workspace'),toolbar=html('div','wp-rel-toolbar');
  const label=html('label','',kind==='people'?'人物を選択':'場所を選択'),select=html('select');select.setAttribute('aria-label',kind==='people'?'図の人物':'図の場所');
  for(const n of graph.nodes){const o=html('option','',n.id);o.value=n.id;o.selected=n.id===selected;select.append(o)}
  select.onchange=()=>onNode(select.value);label.append(select);toolbar.append(label);
  toolbar.append(html('span','diagram-hint',kind==='people'?'世界設定と同じ所属別の相関図':'点は場所、矢印は移動できる向き'));
  root.append(toolbar);
  const body=html('div','wp-rel-body'),wrap=html('div','wp-rel-canvas-wrap');
  const layout=kind==='people'?WorldRelations.layoutRelationGraph(graph,anchor):placeLayout(graph),byId=new Map(layout.nodes.map(n=>[n.id,n]));
  const canvas=svg('svg',{class:'wp-rel-canvas',viewBox:`0 0 ${layout.width} ${layout.height}`,role:'group','aria-label':kind==='people'?'人物相関図':'場所と経路の図'});
  const prefix=`draft-arrow-${++serial}`,defs=svg('defs');
  const marker=svg('marker',{id:prefix,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:8,markerHeight:8,orient:'auto-start-reverse',markerUnits:'userSpaceOnUse'});
  marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'var(--ws-confirm)'}));defs.append(marker);canvas.append(defs);
  const viewport=svg('g',{'data-diagram-viewport':''});canvas.append(viewport);
  for(const g of layout.groups){const el=svg('g',{class:`wp-rel-group wp-rel-color-${g.index%4}`});el.append(svg('rect',{x:g.x,y:g.y,width:g.width,height:g.height,rx:72}),svg('text',{x:g.x+g.width/2,y:g.y+35},g.name));viewport.append(el)}
  graph.edges.forEach((edge,index)=>{
    const a=byId.get(edge.source),b=byId.get(edge.target);if(!a||!b)return;
    const peers=graph.edges.filter(e=>e.source===edge.source&&e.target===edge.target),peer=peers.indexOf(edge);
    const reverse=graph.edges.some(e=>e.source===edge.target&&e.target===edge.source);
    const dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy)||1,ux=dx/len,uy=dy/len;
    const bend=(reverse?38:0)+peer*38,cx=(a.x+b.x)/2-uy*bend,cy=(a.y+b.y)/2+ux*bend;
    const startLen=Math.hypot(cx-a.x,cy-a.y)||1,endLen=Math.hypot(b.x-cx,b.y-cy)||1;
    const sx=a.x+(cx-a.x)/startLen*48,sy=a.y+(cy-a.y)/startLen*48,ex=b.x-(b.x-cx)/endLen*50,ey=b.y-(b.y-cy)/endLen*50;
    const path=a===b?`M ${a.x-25} ${a.y-38} C ${a.x-100} ${a.y-135} ${a.x+100} ${a.y-135} ${a.x+25} ${a.y-38}`:`M ${sx} ${sy} Q ${cx} ${cy} ${ex} ${ey}`;
    const active=edge.source===selected||edge.target===selected,group=svg('g',{class:`wp-rel-edge diagram-edge ${active?'is-active':''} ${Number(edge.affinity)<0?'is-negative':''}`,'data-source':edge.source,'data-target':edge.target});
    group.append(svg('title',{},`${edge.source} → ${edge.target}：${edge.label||'ラベルなし'}`));
    group.append(svg('path',{d:path,class:'diagram-edge-line','marker-end':`url(#${prefix})`}));
    const tx=a===b?a.x:(sx+2*cx+ex)/4,ty=a===b?a.y-105:(sy+2*cy+ey)/4-10;
    group.append(svg('text',{x:tx,y:ty},edge.label||'ラベルなし'));
    if(onEdge){group.setAttribute('role','button');group.setAttribute('tabindex','0');group.setAttribute('aria-label',`${edge.source}から${edge.target}への経路を見る`);group.append(svg('path',{d:path,class:'diagram-edge-hit'}));const open=()=>onEdge(edge.index??index);group.addEventListener('click',open);group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}})}
    viewport.append(group);
  });
  const connected=new Set(graph.edges.filter(e=>e.source===selected||e.target===selected).flatMap(e=>[e.source,e.target]));
  for(const n of layout.nodes){
    const el=svg('g',{class:`wp-rel-node wp-rel-color-${n.groupIndex%4} ${n.id===selected?'is-selected':connected.has(n.id)?'is-connected':'is-unrelated'}`,role:'button',tabindex:0,'aria-label':n.id,'aria-pressed':n.id===selected,transform:`translate(${n.x} ${n.y})`});
    el.append(svg('title',{},n.id),svg('circle',{r:43}));
    const text=svg('text');if(n.id.length>5){text.append(svg('tspan',{x:0,dy:-10},n.id.slice(0,4)),svg('tspan',{x:0,dy:23},n.id.slice(4,8)+(n.id.length>8?'…':'')))}else text.textContent=n.id;el.append(text);
    const open=()=>onNode(n.id);el.addEventListener('click',open);el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}});viewport.append(el);
  }
  wrap.append(canvas);
  let zoom=1,panX=0,panY=0,drag=null;
  function transform(){viewport.setAttribute('transform',`translate(${layout.width/2+panX} ${layout.height/2+panY}) scale(${zoom}) translate(${-layout.width/2} ${-layout.height/2})`)}
  const controls=html('div','wp-rel-controls');
  for(const [title,aria,change] of [['−','図を縮小',()=>{zoom=Math.max(.5,zoom/1.25)}],['＋','図を拡大',()=>{zoom=Math.min(3,zoom*1.25)}],['画面に合わせる','図を画面に合わせる',()=>{zoom=1;panX=0;panY=0}]]){const b=html('button','',title);b.type='button';b.setAttribute('aria-label',aria);b.onclick=()=>{change();transform()};controls.append(b)}wrap.append(controls);
  canvas.addEventListener('pointerdown',e=>{if(e.target.closest('[role="button"]'))return;drag={x:e.clientX,y:e.clientY,px:panX,py:panY};canvas.setPointerCapture(e.pointerId)});
  canvas.addEventListener('pointermove',e=>{if(!drag)return;const scale=Math.max(layout.width/canvas.clientWidth,layout.height/canvas.clientHeight);panX=drag.px+(e.clientX-drag.x)*scale;panY=drag.py+(e.clientY-drag.y)*scale;transform()});
  canvas.addEventListener('pointerup',()=>{drag=null});canvas.addEventListener('pointercancel',()=>{drag=null});
  body.append(wrap);root.append(body);
  if(!graph.nodes.length)root.append(html('p','ui-notice','表示する対象がありません。'));
  for(const issue of graph.issues)root.append(html('p','ui-notice',issue));
  return root;
}

window.WorldBloomDraftWorkspace=function(root,options={}){
root.innerHTML='<div class="ui-workspace ui-workspace--nav draft-workspace"><nav id="draft-nav" class="ui-nav" aria-label="下案の項目"></nav><section id="draft-content" class="ui-content draft-content" tabindex="-1" aria-labelledby="section-title"></section></div><div class="draft-section-footer"><span id="section-position"></span><button id="previous-section" class="ui-button" type="button">前の項目</button><button id="next-section" class="ui-button" type="button">次の項目へ</button></div>';
const $=id=>root.querySelector('#'+id),node=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el};
const sections=[['overview','概要・全体像'],['people','登場人物'],['places','場所'],['items','品物'],['facts','情報'],['story','初期物語'],['relations','人物の関係'],['routes','経路'],['defaults','時間・基本設定']];
let draft,section='overview',person=null,place=null,item=null,route=0,autoEditedHash=null,displayedHash=location.hash;
const content=$('draft-content');
function jump(target,entity){navigate(target,entity);content.focus({preventScroll:true})}
function link(label,target,entity){const b=node('button','inline-link',label);b.type='button';b.onclick=()=>jump(target,entity);return b}
function textLink(label,target,entity){const b=node('button','text-link',label);b.type='button';b.onclick=()=>jump(target,entity);return b}
function card(title,field){const el=node('section','content-card');if(field)el.dataset.editField=field;if(title)el.append(node('h3','',title));return el}
function heading(title,description,count){const head=node('header','section-heading'),body=node('div');const h=node('h2','',title);h.id='section-title';body.append(h);if(description)body.append(node('p','',description));head.append(body);if(count!==undefined)head.append(node('span','count',count));content.append(head)}
function facts(rows){const dl=node('dl','facts-list');for(const [label,value]of rows){const dd=node('dd');if(value instanceof Node)dd.append(value);else dd.textContent=value;dl.append(node('dt','',label),dd)}return dl}
function role(p){return p.name===draft.protagonist?'主人公':p.name===draft.antagonist?'相手役':'登場人物'}
function goal(p){if(typeof p.goal?.description==='string')return p.goal.description||'目標の記述はありません';return p.goal?.target?`${p.goal.target}を${p.goal.deliver_to}へ届ける`:'目標の記述はありません'}
function conditions(p){if(p.goal?.target)return[{type:'acquire',item:p.goal.target},{type:'reach',place:p.goal.deliver_to}];return p.goal?.conditions||[]}
function conditionText(c){return({reach:()=>`${c.place}にいる`,acquire:()=>`${c.item}を所持する`,deliver:()=>`${c.item}を所持して${c.place}にいる`,meet:()=>`${c.person}と同じ場所にいる`,befriend:()=>`${c.person}との双方の親しさが${c.threshold}以上`,defeat:()=>`${c.person}が戦闘不能または死亡する`,survive:()=>`${c.day}日目の夜まで生存する`,discover:()=>`${c.fact}を知る`}[c.type]?.()||'条件の内容を確認してください')}
function chips(p){const list=node('ul','condition-list');for(const c of conditions(p))list.append(node('li','',conditionText(c)));if(!list.children.length)list.append(node('li','','自動判定する条件の指定なし'));return list}
function avatar(name){return node('span','person-avatar',name.slice(0,1))}
function overview(){
 heading('下案の全体像','世界の概要と、人物が目指すことを見渡します。');
 const about=card('世界の概要');about.append(node('p','overview-copy',draft.overview));content.append(about);
 const people=node('div','center-people');for(const p of draft.people){const box=node('article','person-summary'+(p.name===draft.protagonist?' is-protagonist':'')),head=node('div','summary-head'),identity=node('div');identity.append(node('span','role',role(p)),node('strong','',p.name));head.append(avatar(p.name),identity);box.append(head,node('p','goal-summary',goal(p)));const start=node('p','subtle','開始位置：');start.append(link(p.entry||'未設定','places',p.entry));box.append(start,textLink('人物と達成条件を見る →','people',p.name));people.append(box)}content.append(people);
 const structure=node('div','structure-grid');for(const[key,title,rows,desc]of[['places','場所',draft.places,draft.places.map(p=>p.name).join('・')],['items','品物',draft.items,draft.items.map(i=>i.name).join('・')],['routes','経路',draft.routes,'場所のつながりと移動条件']]){const button=node('button','structure-button'),label=node('strong','',title);label.append(node('b','',String(rows.length)));button.type='button';button.append(label,node('span','',desc),node('span','','詳しく見る →'));button.onclick=()=>jump(key);structure.append(button)}content.append(structure);
 const roles=card('主人公と相手役','roles');roles.append(facts([['主人公',link(draft.protagonist,'people',draft.protagonist)],['相手役',link(draft.antagonist,'people',draft.antagonist)]]));content.append(roles);
 const tail=node('div','overview-bottom');tail.append(node('p','','下案の内容を確認中です。まだ世界として採用していません。'),textLink('初期物語を読む →','story'));content.append(tail);
}
function masterList(rows,selected,type,small){const list=node('div','entity-list');list.setAttribute('aria-label',sections.find(s=>s[0]===type)[1]+'を選ぶ');for(const row of rows){const b=node('button','entity-button'),caption=node('span');b.type='button';b.setAttribute('aria-pressed',String(selected===row.name));if(type==='people')b.append(avatar(row.name));caption.append(node('strong','',row.name),node('small','',small(row)));b.append(caption);b.onclick=()=>jump(type,row.name);list.append(b)}return list}
function people(){
 heading('登場人物','人物像、開始位置、目標と達成条件を確認します。',`${draft.people.length}人`);
 const p=draft.people.find(p=>p.name===person)||draft.people[0],grid=node('div','master-detail');if(!p)return;
 grid.append(masterList(draft.people,p.name,'people',role));const detail=node('div','entity-detail'),intro=card(null,'profile'),title=node('h3','entity-title');title.append(node('strong','',p.name),node('span','',role(p)));intro.append(title,node('p','',p.description),facts([['人物像',p.personality||'記述なし'],['開始位置',link(p.entry||'未設定','places',p.entry)]]));detail.append(intro);
 const g=card('目標と達成条件','goal');g.append(node('p','goal-summary',goal(p)),chips(p));if(conditions(p).length>1)g.append(node('p','subtle','達成条件は、すべて同時に満たす設定です。'));detail.append(g);
 const relevant=card('関連する設定'),refs=node('div','ref-list');for(const c of conditions(p)){for(const [key,target]of[['item','items'],['place','places'],['person','people']])if(c[key]){const row=node('div','ref-row');row.append(node('span','',key==='item'?'目標に関わる品物':key==='place'?'目標に関わる場所':'目標に関わる人物'),link(c[key]+' →',target,c[key]));refs.append(row)}}for(const r of p.relations||[]){const row=node('div','ref-row');row.append(node('span','',`${p.name} → ${r.person}：${r.label||'ラベルなし'}`),link('関係を確認 →','relations'));refs.append(row)}if(!refs.children.length)refs.append(node('p','subtle','関連する設定の記述はありません。'));relevant.append(refs);detail.append(relevant);grid.append(detail);content.append(grid);
}
function places(){
 heading('場所','場所の説明、初期位置、移動できる経路を確認します。',`${draft.places.length}か所`);const p=draft.places.find(p=>p.name===place)||draft.places[0];if(!p)return;
 const grid=node('div','diagram-detail');grid.append(diagram({graph:placeGraph(draft),kind:'places',selected:p.name,onNode:name=>jump('places',name),onEdge:index=>jump('routes',String(index))}));const detail=node('div','entity-detail'),about=card(p.name,'description');about.append(node('p','',p.description));detail.append(about);const starters=card('ここから始まる人物'),names=node('div','ref-list');for(const person of draft.people.filter(x=>x.entry===p.name)){const row=node('div','ref-row');row.append(node('span','',role(person)),link(person.name+' →','people',person.name));names.append(row)}if(!names.children.length)names.append(node('p','subtle','開始位置にしている人物はいません。'));starters.append(names);detail.append(starters);
 const exits=card('ここから移動できる場所');draft.routes.forEach((r,i)=>{if(r.from!==p.name)return;const row=node('div','ref-row');row.append(node('span','',`${r.from} → ${r.to}`),link('経路を見る →','routes',String(i)));exits.append(row)});if(exits.children.length===1)exits.append(node('p','subtle','出発する経路はありません。'));detail.append(exits);grid.append(detail);content.append(grid,node('p','subtle','図の配置は接続を示すためのものです。地理的な位置や距離は表していません。往路と復路は、それぞれ別の矢印で確認できます。'));
}
function items(){
 heading('品物','誰が持ち、どの人物の目標に関わるかを確認します。',`${draft.items.length}点`);const v=draft.items.find(i=>i.name===item)||draft.items[0];if(!v){content.append(node('p','','品物の指定はありません。'));return}
 const grid=node('div','master-detail');grid.append(masterList(draft.items,v.name,'items',x=>'所持：'+x.holder));const detail=node('div','entity-detail'),about=card(v.name,'holder');about.append(facts([['持っている人物',link(v.holder,'people',v.holder)]]));detail.append(about);const goals=card('この品物が関わる目標');for(const p of draft.people.filter(p=>conditions(p).some(c=>c.item===v.name))){const row=node('div','ref-row');row.append(node('span','',`${p.name}：${goal(p)}`),link('目標を見る →','people',p.name));goals.append(row)}if(goals.children.length===1)goals.append(node('p','subtle','目標には指定されていません。'));detail.append(goals);grid.append(detail);content.append(grid);
}
function information(){
 const rows=draft.facts||[];heading('情報','調べて得る内容と、調べる場所を確認します。',`${rows.length}件`);
 if(!rows.length){content.append(node('p','ui-notice','情報の指定はありません。'));return;}
 for(const fact of rows){const detail=card(fact.name);detail.append(node('p','reading-copy',fact.description),facts([['調べる場所',link(fact.place,'places',fact.place)]]));content.append(detail);}
}
function story(){heading('初期物語','シミュレーションが始まる直前の状況です。');const prose=card('物語の始まり','story');prose.append(node('p','reading-copy',draft.initial_story||'記述なし'));content.append(prose);const initial=card('開始時点の人物'),table=node('table','plain-table'),thead=node('thead'),tr=node('tr');for(const t of['人物','開始位置','持っている品物'])tr.append(node('th','',t));thead.append(tr);table.append(thead);const tbody=node('tbody');for(const p of draft.people){const row=node('tr');for(const el of[link(p.name,'people',p.name),link(p.entry,'places',p.entry),document.createTextNode(draft.items.filter(i=>i.holder===p.name).map(i=>i.name).join('・')||'指定なし')]){const td=node('td');td.append(el);row.append(td)}tbody.append(row)}table.append(tbody);initial.append(table);content.append(initial)}
function relations(){
 heading('人物の関係','世界設定と同じ相関図で、人物の所属と関係を確認します。');
 const graph=draftPeopleGraph(draft),p=draft.people.find(p=>p.name===person)||draft.people[0];if(!p)return;
 const grid=node('div','diagram-detail');grid.append(diagram({graph,selected:p.name,anchor:draft.protagonist,onNode:name=>jump('relations',name)}));
 const detail=node('div','entity-detail'),about=card(p.name);about.append(node('p','',p.description),facts([['役割',role(p)],['所属・開始位置',p.affiliation||p.entry||'その他']]),link('人物の詳細を見る →','people',p.name));detail.append(about);
 const refs=card('選んだ人物の関係');let count=0;for(const e of graph.edges.filter(e=>e.source===p.name||e.target===p.name)){count++;const row=node('section','relation-detail');row.dataset.editField='relation';row.dataset.editEntity=e.source;row.dataset.editPeer=e.target;row.append(node('h4','',`${e.source} → ${e.target}`),node('p','',e.label||'ラベルなし'));const numbers=node('details');numbers.append(node('summary','','関係の数値を見る'),facts([['親しさ',String(e.affinity??'未設定')],['認知',String(e.awareness??'未設定')]]));row.append(numbers);refs.append(row)}if(!count)refs.append(node('p','subtle','この人物に関する関係の指定はありません。'));detail.append(refs);grid.append(detail);content.append(grid,node('p','subtle','矢印は関係の向きを示します。逆方向の関係は、別の設定として確認します。'));
}
function routes(){
 heading('経路','出発地と到着地、移動の条件を確認します。',`${draft.routes.length}経路`);const selected=draft.routes[route]||draft.routes[0];if(!selected){content.append(node('p','','経路の指定はありません。'));return}
 const list=node('div','entity-list');draft.routes.forEach((r,i)=>{const b=node('button','entity-button',`${r.from} → ${r.to}`);b.type='button';b.setAttribute('aria-pressed',String(r===selected));b.onclick=()=>jump('routes',String(i));list.append(b)});const grid=node('div','master-detail');grid.append(list);const detail=node('div','entity-detail'),graph=node('div','route-graph'),a=node('div','graph-node'),b=node('div','graph-node');a.append(link(selected.from,'places',selected.from));b.append(link(selected.to,'places',selected.to));graph.append(a,node('span','graph-arrow','→'),b);detail.append(graph);const info=card('移動の条件','route');info.append(facts([['向き',`${selected.from} → ${selected.to}`],['コスト',String(selected.cost??1)+(selected.cost===undefined?'（既定値）':'')],['必要な品物',selected.requires_item?link(selected.requires_item,'items',selected.requires_item):'指定なし']]));detail.append(info);const reverse=draft.routes.find(r=>r.from===selected.to&&r.to===selected.from);detail.append(node('p','subtle',reverse?'戻る方向の経路も、別の設定として登録されています。':'戻る方向の経路は登録されていません。'));grid.append(detail);content.append(grid);
}
function defaults(){heading('時間・基本設定','この下案に適用する初期値を確認します。');const time=card('期間と時間帯');time.append(facts([['期間','7日間（既定値）']]));const bands=node('div','time-bands');for(const t of['朝','昼','夕','夜'])bands.append(node('span','',t));time.append(bands);content.append(time);const numbers=card('数値特性の初期値');numbers.append(facts([['各数値特性','0.5'],['基礎値','50']]));content.append(numbers,node('p','ui-notice','共通の基本ルールを使う既定値です。概要の文章から推定した数値ではありません。'))}
function renderSection(){
 switch(section){
  case 'people':people();break;
  case 'places':places();break;
  case 'items':items();break;
  case 'facts':information();break;
  case 'story':story();break;
  case 'relations':relations();break;
  case 'routes':routes();break;
  case 'defaults':defaults();break;
  default:overview();
 }
}
function render(){
 const nav=$('draft-nav');nav.replaceChildren();
 for(const [id,title]of sections){const button=node('button','',title);button.type='button';button.dataset.section=id;if(section===id)button.setAttribute('aria-current','page');const count=id==='people'?draft.people.length:id==='places'?draft.places.length:id==='items'?draft.items.length:id==='routes'?draft.routes.length:null;if(count!==null)button.append(node('small','',String(count)));button.onclick=()=>navigate(id);nav.append(button)}
 content.replaceChildren();renderSection();content.scrollTop=0;
 const index=sections.findIndex(s=>s[0]===section);$('section-position').textContent=`${index+1} / ${sections.length}`;$('previous-section').disabled=index===0;$('next-section').disabled=index===sections.length-1;$('next-section').textContent=index===sections.length-1?'最後の項目です':'次の項目へ →';
 if(options.afterRender)options.afterRender({section,entity:section==='people'||section==='relations'?(person||draft.people[0]?.name):section==='places'?(place||draft.places[0]?.name):section==='items'?(item||draft.items[0]?.name):section==='routes'?String(route):''},content);
}
function navigate(target,entity,push=true){
 if(push&&options.beforeNavigate&&!options.beforeNavigate())return;
 section=sections.some(s=>s[0]===target)?target:'overview';if(entity){if(section==='people'||section==='relations')person=entity;if(section==='places')place=entity;if(section==='items')item=entity;if(section==='routes')route=Number(entity)||0}
 const hash=new URLSearchParams({section});const selected=section==='people'||section==='relations'?person:section==='places'?place:section==='items'?item:section==='routes'?String(route):null;if(selected)hash.set('entity',selected);
 if(push)history.pushState(null,'','#'+hash);displayedHash=location.hash;render();
}
function restore(fromHistory=false){
 if(fromHistory&&options.beforeNavigate&&!options.beforeNavigate()){
  // Reconcile the current entry synchronously. A compensating history.go()
  // would queue another traversal that can race a second Back/Forward action.
  history.replaceState(history.state,'',location.pathname+location.search+displayedHash);return;
 }
 const hash=new URLSearchParams(location.hash.slice(1));navigate(hash.get('section')||'overview',hash.get('entity'),false);if(hash.get('edit')&&autoEditedHash!==location.hash){autoEditedHash=location.hash;Array.from(content.querySelectorAll('[data-field-pencil]')).find(b=>b.dataset.fieldPencil===hash.get('edit'))?.click()}
}
$('previous-section').onclick=()=>{const i=sections.findIndex(s=>s[0]===section);if(i>0)navigate(sections[i-1][0])};$('next-section').onclick=()=>{const i=sections.findIndex(s=>s[0]===section);if(i<sections.length-1)navigate(sections[i+1][0])};window.addEventListener('popstate',()=>{if(draft)restore(true)});

return {select(target,entity){draft=options.getDraft?options.getDraft():draft;navigate(target,entity)},render(value){draft=structuredClone(value);restore()},setDisabled(value){root.querySelectorAll('button,select').forEach(el=>{if(value){if(el.dataset.wasDisabled===undefined)el.dataset.wasDisabled=String(el.disabled);el.disabled=true}else if(el.dataset.wasDisabled!==undefined){el.disabled=el.dataset.wasDisabled==='true';delete el.dataset.wasDisabled}})}};
};
})();
