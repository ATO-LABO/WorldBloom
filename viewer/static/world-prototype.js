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
if(typeof module !== 'undefined' && module.exports) module.exports=WorldRelations;

/* Saved world settings. No simulation or generation is started here. */
(() => {
  'use strict';
  if(typeof document === 'undefined') return;
  const root = document.querySelector('[data-world-prototype]');
  if (!root) return;
  const model = JSON.parse(document.getElementById('wp-data').textContent);
  const content = document.getElementById('wp-content');
  const dialog = root.querySelector('dialog');
  const form = document.getElementById('wp-form');
  let w = model.world;
  let people = model.people.filter(p => p.id);
  let zones = (w.zones || []).map(z => typeof z === 'string' ? {name:z,note:''} : {...z});
  people.sort((a,b) => Number(b.id === w.protagonist) - Number(a.id === w.protagonist));
  let screen = 'overview', person = 0, place = Math.max(0,zones.findIndex(z=>z.name==='海')), eventDay = null;
  let peopleView = 'list', placeView = 'map', storyView = 'intro', query = '';
  let graphMode='all', relationScope='all', relationGroup='', relationKind='all', relationInspector=true, relationExpanded=false;
  let relationZoom=1, relationPan={x:0,y:0}, relationDrag=null;
  let applyEdit = null, opener = null, changed = false, busy = false, blocked = false;
  const scrolls = {};
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const absent = v => esc(v || '未入力');
  const items = p => Object.entries(p.inventory || {}).map(([k,v])=>`${k} × ${v}`).join('、') || 'なし';
  const knows = p => (p.knowledge || []).join('、') || 'なし';
  const entry = p => p.range?.entry || '未設定';
  const goal = p => (p?.goal?.target ? `${p.goal.target}${p.goal.deliver_to ? `を${p.goal.deliver_to}へ届ける` : 'を求める'}` : '未入力');
  const role = p => p.id === w.protagonist ? '主人公' : p.id === w.antagonist ? '敵役' : '';
  const genreName = id => model.genres.find(g=>g.id===id)?.name || id;
  const edit = (key,label='編集') => model.editable ? `<button type="button" data-edit="${key}">✎ ${label}</button>` : '';
  const head = (title,action='') => `<div class="wp-section-head"><h2>${title}</h2>${action}</div>`;
  const title = (name,sub='',action='') => `<div class="wp-title"><h1 tabindex="-1">${esc(name)}</h1>${action}</div>${sub?`<p class="wp-subtitle">${esc(sub)}</p>`:''}`;
  const row = (name,value) => `<div><dt>${esc(name)}</dt><dd>${value}</dd></div>`;
  const tabs = (group,selected,entries) => `<div class="wp-tabs" aria-label="表示の切り替え">${entries.map(([key,label])=>`<button type="button" data-view="${group}:${key}" aria-pressed="${selected===key}">${label}</button>`).join('')}</div>`;
  const time = () => `${w.time?.days ?? '未指定'}日間 ・ ${(w.time?.slots||[]).join(' ／ ')}`;
  function overview() {
    const hero = people.find(p=>p.id===w.protagonist);
    // V2 (viewer review): "後から生まれたもの" (承認済み/提案中の世界拡張)
    // ships as an inert <template id="wp-expansion"> (viewer/world_prototype.py)
    // so it renders inside 世界の概要 instead of sitting as a stray sibling
    // of #wp-content in the .wp 2-column grid. Its markup is server-built
    // and already HTML-escaped (viewer/world_expansion_view.py), so
    // innerHTML here doesn't need its own escaping. Absent when there's
    // nothing to show (template tag itself is omitted server-side then).
    const expansionTemplate = document.getElementById('wp-expansion');
    const expansionHtml = expansionTemplate ? expansionTemplate.innerHTML : '';
    return title(model.name,'この世界の前提を確認する') +
      `<section class="wp-section">${head('どんな世界？',edit('overview'))}<p>${absent(model.overview)}</p></section>`+
      `<section class="wp-section">${head('ジャンル',edit('genre'))}<p>${model.genre ? esc(genreName(model.genre)) : '未設定（実行前に選んでください）'}</p></section>`+
      `<div class="wp-columns"><section><h2>物語の出発点</h2><p>${hero?`${esc(hero.id)}は${esc(entry(hero))}にいる。`:'主人公は未指定です。'}</p></section><section><h2>目指す結末</h2><p>${esc(goal(hero))}</p></section></div>`+
      `<section class="wp-section">${head('この世界を構成するもの')}<button class="wp-jump" data-screen="people"><strong>登場人物</strong><span>${esc(people.map(p=>p.id).join('、')) || 'まだいません'}</span><span>→</span></button><button class="wp-jump" data-screen="places"><strong>場所</strong><span>${esc(zones.map(z=>z.name).join('、')) || 'まだありません'}</span><span>→</span></button><button class="wp-jump" data-screen="story"><strong>初期物語</strong><span>シミュレーション開始時点の導入・状況</span><span>→</span></button><button class="wp-jump" data-screen="time"><strong>時間</strong><span>${esc(time())}</span><span>→</span></button></section>`+
      `<details class="wp-section"><summary>詳しい世界設定</summary><p>状況別の定石・行動図鑑・設定ファイルは、<a href="/worlds/${encodeURIComponent(model.id)}?view=advanced">詳細設定</a>で確認できます。</p></details>`+
      expansionHtml;
  }
  const TRAIT_LABELS = {social:'社交性',stubbornness:'頑固さ',curiosity:'好奇心',diligence:'勤勉さ',temper:'気性の荒さ'};
  const num = v => Number(v) || 0;
  const bar = (label, value, scale, text) => {
    const ratio = scale > 0 ? Math.max(0, Math.min(1, value / scale)) : 0;
    return `<div class="stat"><span class="stat-label">${esc(label)}</span><span class="gene-track"><span class="gene-fill" style="width:${(ratio*100).toFixed(1)}%"></span></span><span class="stat-value">${esc(text)}</span></div>`;
  };
  // Same nine bars and scales as the advanced sheet (viewer/world_graph.py).
  function statBars(p) {
    const traits = p.traits || {};
    const stamina = p.stamina || {};
    const baseScale = Math.max(100, ...people.map(q => num(q.base)));
    const staminaScale = Math.max(0, ...people.map(q => num((q.stamina || {}).max)));
    const allyScale = Math.max(0, ...people.map(q => num(q.ally_value)));
    const rows = Object.entries(TRAIT_LABELS).map(([k, label]) => bar(label, num(traits[k]), 1, num(traits[k]).toFixed(2)));
    rows.push(bar('基礎の強さ', num(p.base), baseScale, String(Math.round(num(p.base)))));
    rows.push(bar('体力', num(stamina.max), staminaScale, `${Math.round(num(stamina.max))}（回復 ${num(stamina.recover_per_slot)}/時間帯）`));
    rows.push(bar('評判', num(p.reputation), 1, num(p.reputation).toFixed(2)));
    rows.push(bar('仲間への加勢', num(p.ally_value), allyScale, String(Math.round(num(p.ally_value)))));
    return '<div class="stat-bars">' + rows.join('') + '</div>';
  }
  function personDetail() {
    const p = people[person];
    if (!p) return '<p>人物を追加すると、ここに詳細が表示されます。</p>';
    const relations = Object.entries(p.relations||{}).map(([name,r])=>`<span>${esc(name)} <small class="wp-muted">${r?.label?esc(r.label):r?.affinity>0?'好意がある':r?.affinity<0?'反感がある':'中立・未設定'}</small></span>`).join('') || '関係は未設定です。';
    return `<div class="wp-section-head"><span class="wp-role">${esc(role(p)||'登場人物')}</span>${edit('person','人物を編集')}</div><h2>${esc(p.id)}</h2><p>${absent(p.description || p.identity?.true || p.identity?.['true'])}</p><dl class="wp-dl">${row('目的',esc(goal(p)))}${row('人物像',absent(p.personality))}${row('ほかの人物との関係',`<div class="wp-relation-list">${relations}</div>`)}${row('持ち物・知っていること',`${esc(items(p))}<br>${esc(knows(p))}`)}</dl><details open><summary>性格・能力の数値</summary>${statBars(p)}</details><details><summary>秘密・他者からの見え方</summary><p>本当の姿: ${absent(p.identity?.true)}<br>表向きの姿: ${absent(p.identity?.displayed)}</p></details>`;
  }
  function personButton(p,i){
    const anchor=WorldRelations.affiliation(people.find(q=>q.id===w.protagonist)||{});
    const groups=[...new Set(people.map(WorldRelations.affiliation))].sort((a,b)=>Number(b===anchor)-Number(a===anchor)||(a<b?-1:a>b?1:0));
    return `<button class="wp-person-button wp-rel-color-${groups.indexOf(WorldRelations.affiliation(p))%4}" data-person="${i}" aria-pressed="${i===person}" ${!p.id.includes(query)?'hidden':''}><span class="wp-person-avatar" aria-hidden="true"></span><span>${esc(p.id)}<small>${esc(role(p))}</small></span></button>`;
  }
  function peopleScreen() {
    const top=title('登場人物','',`<span>${people.length}人</span>${model.editable?'<button class="wp-add" data-edit="add-person">＋ 人物を追加</button>':''}`);
    return top+`<div class="wp-people wp-rel-people"><aside class="wp-people-list"><input class="wp-search" type="search" aria-label="人物を探す" placeholder="人物を探す" value="${esc(query)}"><div class="wp-person-options">${people.map(personButton).join('')}</div><p class="wp-muted" id="wp-no-results" ${people.some(p=>p.id.includes(query))?'hidden':''}>一致する人物がいません。</p></aside><article class="wp-person-detail">${tabs('people',peopleView,[['list','一覧'],['graph','相関図'],['matrix','関係表']])}${peopleBody()}</article></div>`;
  }
  function peopleBody(){return peopleView==='graph'?relationsView():peopleView==='matrix'?relationMatrixView():personDetail();}
  const relationText = e => `${e.source} → ${e.target}：${e.label||'ラベルなし'} ／ 親しさ ${e.affinity??'未設定'} ／ 認知 ${e.awareness??'未設定'}`;
  function relationRows(graph,focusId) {
    const edges=graph.edges.filter(e=>!focusId||e.source===focusId||e.target===focusId);
    return edges.map(e=>`<div class="wp-rel-row"><span>${esc(relationText(e))}</span>${model.editable?`<button data-relation-source="${esc(e.source)}" data-relation-target="${esc(e.target)}" aria-label="${esc(e.source)}から${esc(e.target)}への関係ラベルを編集">ラベルを編集</button>`:''}</div>`).join('')||'<p class="wp-muted">関係は未設定です。</p>';
  }
  function relationMatrixView() {
    const graph=WorldRelations.buildRelationGraph(people), lookup=new Map(graph.edges.map(e=>[JSON.stringify([e.source,e.target]),e]));
    return `<section class="wp-rel-matrix-view"><h2>関係表</h2><p class="wp-muted">行の人物 → 列の人物の関係です。逆方向は反対のセルで確認できます。「—」は関係なしです。</p><div class="wp-rel-matrix" tabindex="0" aria-label="人物の方向別関係表"><table><thead><tr><th scope="col">人物 → 相手</th>${graph.nodes.map(n=>`<th scope="col">${esc(n.id)}</th>`).join('')}</tr></thead><tbody>${graph.nodes.map(a=>`<tr><th scope="row"><button data-person="${people.findIndex(p=>p.id===a.id)}">${esc(a.id)}</button></th>${graph.nodes.map(b=>{
      const e=lookup.get(JSON.stringify([a.id,b.id]));
      return `<td ${a.id===b.id?'class="wp-rel-self"':''}>${a.id===b.id?'本人':!e?'—':`<span>${esc(e.label||'ラベルなし')}</span><small>親しさ ${esc(e.affinity??'未設定')}<br>認知 ${esc(e.awareness??'未設定')}</small>${model.editable?`<button data-relation-source="${esc(a.id)}" data-relation-target="${esc(b.id)}" aria-label="${esc(a.id)}から${esc(b.id)}への関係ラベルを編集">編集</button>`:''}`}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div>${graph.issues.length?`<p class="wp-muted">${esc(graph.issues.join(' ／ '))}</p>`:''}</section>`;
  }
  function relationsView() {
    const graph=WorldRelations.buildRelationGraph(people), focusId=people[person]?.id||'';
    const filtered=WorldRelations.filterRelationGraph(graph,{mode:graphMode,focusId,scope:relationScope,group:relationGroup,kind:relationKind});
    const layout=WorldRelations.layoutRelationGraph(filtered,w.protagonist), byId=new Map(layout.nodes.map(n=>[n.id,n]));
    const pairs=WorldRelations.buildVisualEdges(filtered), groups=[...new Set(graph.nodes.map(n=>n.group))].sort();
    const connected=new Set(graph.edges.filter(e=>e.source===focusId||e.target===focusId).flatMap(e=>[e.source,e.target]).filter(id=>id!==focusId));
    const p=people[person], major=[...connected].map(id=>({id,edges:graph.edges.filter(e=>(e.source===focusId&&e.target===id)||(e.target===focusId&&e.source===id))})).sort((a,b)=>Math.max(...b.edges.map(e=>Math.abs(Number(e.affinity)||0)))-Math.max(...a.edges.map(e=>Math.abs(Number(e.affinity)||0))));
    const select=(name,value,options,label)=>`<label>${label}<select data-rel-select="${name}" aria-label="${label}">${options.map(([v,l])=>`<option value="${esc(v)}" ${value===v?'selected':''}>${esc(l)}</option>`).join('')}</select></label>`;
    const header=`<h2>相関図</h2><p class="wp-rel-description">登場人物同士の関係を視覚的に確認できます。グループ分けや、人物同士のつながりも表示されます。</p>`;
    const toolbar=`<div class="wp-rel-toolbar">${select('focus',focusId,[['','人物を選択してフォーカス…'],...graph.nodes.map(n=>[n.id,n.id])],'フォーカス人物')}<div class="wp-rel-segments" aria-label="グラフ表示">${[['all','全体'],['focus','人物フォーカス']].map(([v,l])=>`<button data-rel-mode="${v}" aria-pressed="${graphMode===v}" ${!p&&v==='focus'?'disabled':''}>${l}</button>`).join('')}</div>${select('kind',relationKind,[['all','すべて'],['positive','好意'],['negative','反感'],['neutral','中立']],'表示関係')}${select('group',relationGroup,[['','すべて'],...groups.map(g=>[g,g])],'所属')}<div class="wp-rel-scope"><span>表示範囲</span><div class="wp-rel-segments">${[['direct','直接関係'],['two','2段階'],['all','すべて']].map(([v,l])=>`<button data-rel-scope="${v}" aria-pressed="${relationScope===v}" ${graphMode==='all'?'disabled':''}>${l}</button>`).join('')}</div></div></div>`;
    const svg=`<svg class="wp-rel-canvas" viewBox="0 0 ${layout.width} ${layout.height}" role="group" aria-label="人物相関図。人物を選ぶと詳細を表示します"><g data-rel-viewport>${layout.groups.map(g=>`<g class="wp-rel-group wp-rel-color-${g.index%4}"><rect x="${g.x}" y="${g.y}" width="${g.width}" height="${g.height}" rx="72"/><text x="${g.x+g.width/2}" y="${g.y+35}">${esc(g.name)}</text></g>`).join('')}${pairs.map(e=>{
      const a=byId.get(e.a),b=byId.get(e.b),active=e.a===focusId||e.b===focusId,negative=e.directions.some(d=>Number(d.affinity)<0);
      const outward=e.directions.find(d=>d.source===focusId), inward=e.directions.find(d=>d.target===focusId);
      const labelPeer=e.a===focusId?e.b:e.a;
      const visibleLabel=active&&major.slice(0,6).some(m=>m.id===labelPeer)?(outward?.label||(inward?.label?'← '+inward.label:'')):'';
      return `<g class="wp-rel-edge ${active?'is-active':''} ${negative?'is-negative':''}"><title>${esc(e.directions.map(relationText).join('\n'))}</title><line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>${visibleLabel?`<text x="${(a.x+b.x)/2}" y="${(a.y+b.y)/2-9}">${esc(visibleLabel)}</text>`:''}</g>`;
    }).join('')}${layout.nodes.map(n=>`<g class="wp-rel-node wp-rel-color-${n.groupIndex%4} ${n.id===focusId?'is-selected':connected.has(n.id)?'is-connected':'is-unrelated'}" role="button" tabindex="0" data-person="${people.findIndex(q=>q.id===n.id)}" aria-pressed="${n.id===focusId}" aria-label="${esc(n.id)}、${esc(n.group)}" transform="translate(${n.x} ${n.y})"><title>${esc(n.id)}・${esc(n.group)}</title><circle r="43"/><text${n.id.length===5?' style="font-size:16px"':''}>${n.id.length>5?`<tspan x="0" dy="-10">${esc(n.id.slice(0,4))}</tspan><tspan x="0" dy="23">${esc(n.id.length>8?n.id.slice(4,7)+'…':n.id.slice(4))}</tspan>`:esc(n.id)}</text></g>`).join('')}</g></svg>`;
    const inspector=p&&relationInspector?`<aside class="wp-rel-inspector"><button class="wp-rel-dismiss" data-rel-inspector="close" aria-label="人物詳細パネルを閉じる">×</button><div class="wp-rel-person-heading"><span class="wp-rel-avatar"></span><div><h3>${esc(p.id)}</h3><small>${esc(role(p)||'登場人物')}</small></div></div><dl>${row('所属',esc(WorldRelations.affiliation(p)))}${row('直接関係',`${connected.size}人`)}${row('概要',absent(p.description||p.identity?.true))}</dl><h3>主な関係</h3><ul>${major.slice(0,6).map(m=>`<li><button data-person="${people.findIndex(q=>q.id===m.id)}">${esc(m.id)}</button><span>${m.edges.map(e=>`<small>${e.source===focusId?'→':'←'} ${esc(e.label||'ラベルなし')}</small>`).join('')}</span></li>`).join('')||'<li>関係は未設定です。</li>'}</ul>${major.length>6?`<p class="wp-muted">ほか${major.length-6}人との関係は下の数値詳細で確認できます。</p>`:''}<button class="wp-rel-detail-link" data-view="people:list">詳細を見る →</button></aside>`:'';
    return header+`<section class="wp-rel-workspace ${relationExpanded?'is-expanded':''}">${toolbar}<div class="wp-rel-body ${inspector?'has-inspector':''}"><div class="wp-rel-canvas-wrap">${svg}${!filtered.nodes.length?'<p class="wp-rel-empty">表示できる人物がいません。</p>':''}<div class="wp-rel-controls"><button data-rel-zoom="out" aria-label="縮小">−</button><button data-rel-zoom="in" aria-label="拡大">＋</button><button data-rel-zoom="fit">画面に合わせる</button><button data-rel-fullscreen aria-pressed="${relationExpanded}" aria-label="${relationExpanded?'相関図の全画面表示を終了':'相関図を全画面表示'}">⛶</button>${!inspector&&p?'<button data-rel-inspector="open">人物詳細</button>':''}</div></div>${inspector}</div></section><details class="wp-rel-numbers"><summary>関係を数値で確認</summary>${relationRows(graph,focusId)}</details>${graph.issues.length?`<details class="wp-rel-numbers"><summary>設定の確認（${graph.issues.length}件）</summary><p>${esc(graph.issues.join('\n'))}</p></details>`:''}`;
  }
  function updateRelationTransform() {
    const viewport=root.querySelector('[data-rel-viewport]');
    if(viewport)viewport.setAttribute('transform',`translate(${relationPan.x} ${relationPan.y}) scale(${relationZoom})`);
  }
  function renderPeoplePanel() {
    const panel=root.querySelector('.wp-person-detail');
    panel.innerHTML=tabs('people',peopleView,[['list','一覧'],['graph','相関図'],['matrix','関係表']])+peopleBody();
    updateRelationTransform();
  }

  const routeLabel = r => `${r.requires_item ? `${r.requires_item}が必要` : '持ち物の条件なし'}${r.cost != null ? ` ・ コスト ${r.cost}` : ''}`;
  function placeDetail(){
    const z=zones[place];if(!z)return '<p>場所を追加すると詳細を表示します。</p>';
    const routes = w.routes?.[z.name] || [];
    const activities = (w.items||[]).filter(i=>i.craft_zone===z.name).map(i=>`${i.name}を作る`);
    return `<p class="wp-muted">選択した場所</p>${head(esc(z.name),edit('place'))}<p>${absent(z.note)}</p><section class="wp-section">${head('ここでできること')}<p>${esc(activities.join('、')||'製作するものの指定はありません。')}</p></section><section class="wp-section">${head('ここから移動できる場所')}${routes.map((r,i)=>`<button class="wp-route" ${model.editable?`data-route="${i}"`:'disabled'}>${esc(z.name)} → ${esc(r.to)}<small>${esc(routeLabel(r))}</small></button>`).join('')||'<p>経路は未設定です。</p>'}</section><p class="wp-muted">往路の条件です。復路は相手の場所を選んで確認できます。</p>`;
  }
  function map(){
    const fixed={村:[10,46],道中:[35,46],森:[60,20],海:[60,76],鬼ヶ島:[88,76]};
    const pts=zones.map((z,i)=>model.id==='momotaro'&&fixed[z.name]?fixed[z.name]:[50+36*Math.cos(i/zones.length*2*Math.PI),50+34*Math.sin(i/zones.length*2*Math.PI)]);
    const lines=[];
    zones.forEach((z,i)=>(w.routes?.[z.name]||[]).forEach(r=>{
      const j=zones.findIndex(t=>t.name===r.to);if(j<0||j===i)return;
      const a=pts[i],b=pts[j],d=Math.hypot(b[0]-a[0],b[1]-a[1]);
      lines.push(`<line x1="${a[0]+(b[0]-a[0])*9/d}" y1="${a[1]+(b[1]-a[1])*9/d}" x2="${b[0]-(b[0]-a[0])*9/d}" y2="${b[1]-(b[1]-a[1])*9/d}" marker-end="url(#wp-arrow)"/>`);
    }));
    return `<div class="wp-map"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><defs><marker id="wp-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#87928c"/></marker></defs>${lines.join('')}</svg>${zones.map((z,i)=>`<button data-place="${i}" aria-pressed="${i===place}" style="left:${pts[i][0]}%;top:${pts[i][1]}%">${esc(z.name)}</button>`).join('')}</div>`;
  }
  function placesScreen(){return title('場所','この世界に登場する場所と、つながりを整理しましょう。',`<span>${zones.length}か所</span>${model.editable?'<button class="wp-add" data-edit="add-place">＋ 場所を追加</button>':''}`)+tabs('places',placeView,[['map','つながり'],['list','一覧']])+`<div class="wp-place-grid"><div>${placeView==='map'?map():`<div class="wp-list">${zones.map((z,i)=>`<button data-place="${i}" aria-pressed="${i===place}">${esc(z.name)}</button>`).join('')}</div>`}<p class="wp-muted">場所を選ぶと詳細を表示します。移動条件は右の経路から確認できます。</p></div><article class="wp-place-detail">${placeDetail()}</article></div>`;}
  function stateTable(){return `<p class="wp-muted">設定ファイルの初期値です。開始時イベント・乱数による変化は実行時に決まります。</p><table class="wp-state"><thead><tr><th>人物</th><th>居場所</th><th>持ち物</th><th>知識</th>${model.editable?'<th class="wp-state-action" aria-label="操作"></th>':''}</tr></thead><tbody>${people.map((p,i)=>`<tr><th><button data-person-link="${i}">${esc(p.id)} ↗</button></th><td>${esc(entry(p))}</td><td>${esc(items(p))}</td><td>${esc(knows(p))}</td>${model.editable?`<td class="wp-state-action"><button data-state-person="${i}" aria-label="${esc(p.id)}の初期状態を編集" title="初期状態を編集">✎</button></td>`:''}</tr>`).join('')}</tbody></table>`;}
  function story(){return title('初期物語','シミュレーションが始まる直前の状況')+tabs('story',storyView,[['intro','導入文'],['state','開始時点の状態']])+(storyView==='intro'?`<section class="wp-section">${head('物語の始まり',edit('intro','導入文を編集'))}<p class="wp-story-copy">${absent(model.intro)}</p></section><button class="wp-route" data-view="story:state">開始時点の状態を確認 →</button>`:`<section class="wp-section">${head('開始時点の状況')}${stateTable()}</section>`)+`<p class="wp-muted wp-section">この先の展開は、シミュレーションで決まります。導入文を編集しても開始状態は変わりません。</p>`;}
  // WB-TIMEEVENT-001: scheduled_events display + edit on the time screen.
  const eventSlotIndex = slot => {
    if(!slot)return 0;
    const slots=w.time?.slots||[];
    const found=slots.indexOf(slot);
    return found<0 ? slots.length+1 : found+1;
  };
  const sortedEvents = () => (w.scheduled_events||[]).map((e,i)=>({...e,__index:i}))
    .sort((a,b)=>(Number(a.day)-Number(b.day))||(eventSlotIndex(a.slot)-eventSlotIndex(b.slot))||String(a.id).localeCompare(String(b.id)));
  function eventDayGrid(events){
    const days=Number(w.time?.days)||0;
    if(!days||days>366)return '';
    const counts={};
    events.forEach(e=>{counts[e.day]=(counts[e.day]||0)+1;});
    const buttons=[];
    for(let d=1;d<=days;d++){
      const count=counts[d]||0;
      buttons.push(`<button type="button" data-event-day="${d}" aria-pressed="${eventDay===d}" ${count?`data-count="${count>9?'9+':count}"`:''}>${d}</button>`);
    }
    return `<div class="wp-event-days">${buttons.join('')}</div>`;
  }
  function eventSummary(e){
    const parts=[];
    if(e.grants_item)parts.push(`${esc(e.grants_item.name)}×${esc(e.grants_item.count??1)} を得る`);
    if(e.grants_fact)parts.push(`${esc(e.grants_fact)} を知る`);
    if(e.move_to)parts.push(`${esc(e.move_to)} へ移る（瞬間移動）`);
    if(e.force_action)parts.push(`行動を『${esc(e.force_action.verb)}${e.force_action.args?.length?' '+esc(e.force_action.args.join(' ')):''}』に固定`);
    if(e.stress_delta)parts.push(`ストレス ${e.stress_delta>0?'+':''}${esc(e.stress_delta)}`);
    return parts.join('、')||'なし';
  }
  function eventsTable(){
    const events=sortedEvents();
    const filtered=eventDay==null?events:events.filter(e=>Number(e.day)===eventDay);
    if(!filtered.length)return '<p class="wp-muted">予定された出来事はありません。</p>';
    const rows=filtered.map(e=>`<tr><td>${esc(e.day)}</td><td>${esc(e.slot||'日の初め')}</td><td>${esc(e.label||e.id)}</td><td>${esc((e.targets||[]).join('、'))}</td><td>${eventSummary(e)}</td>${model.editable?`<td class="wp-state-action"><button data-edit="event:${e.__index}" aria-label="編集" title="編集">✎</button><button data-edit="remove-event:${e.__index}" aria-label="削除" title="削除">✕</button></td>`:''}</tr>`).join('');
    return `<table class="wp-state wp-events"><thead><tr><th>日</th><th>時間帯</th><th>名前</th><th>対象</th><th>内容</th>${model.editable?'<th class="wp-state-action" aria-label="操作"></th>':''}</tr></thead><tbody>${rows}</tbody></table>`;
  }
  function timeScreen(){
    const days=Number(w.time?.days)||0;
    const slots=w.time?.slots||[];
    const cycle=slots.length?slots:['時間帯は未設定'];
    const events=sortedEvents();
    return title('時間','シミュレーションの長さと、1日の進み方',edit('time','時間を編集'))+
      `<div class="wp-time-summary"><section><span class="wp-time-label">物語の期間</span><strong>${days?`${esc(days)}日間`:'未設定'}</strong><p>この期間の中で、人物が行動し物語が進みます。</p></section><section><span class="wp-time-label">1日の区切り</span><strong>${slots.length?`${esc(slots.length)}区切り`:'未設定'}</strong><p>${slots.length?'1日の中を複数の時間帯に分けて進めます。':'時間帯を設定してください。'}</p></section></div>`+
      `<section class="wp-section">${head('1日の流れ')}<ol class="wp-time-cycle">${cycle.map((slot,index)=>`<li><span>${index+1}</span><strong>${esc(slot)}</strong>${index<cycle.length-1?'<i aria-hidden="true">→</i>':''}</li>`).join('')}</ol><p class="wp-muted">最後の時間帯が終わると、次の日の最初の時間帯へ進みます。</p></section>`+
      `<section class="wp-section">${head('設定内容')}<dl class="wp-dl">${row('日数',days?`${esc(days)}日間`:'未設定')}${row('時間帯',esc(slots.join(' ／ ')||'未設定'))}</dl></section>`+
      `<section class="wp-section">${head('予定された出来事',model.editable?'<button class="wp-add" data-edit="add-event">＋ 出来事を追加</button>':'')}${eventDayGrid(events)}${eventsTable()}</section>`;
  }
  let routesData=null, routesLoading=false, routesTimepoint=0;
  function loadRoutes(){
    if(routesLoading||routesData)return;
    routesLoading=true;
    fetch(`/api/worlds/${encodeURIComponent(model.id)}/routes`).then(r=>{
      if(!r.ok)return r.json().catch(()=>null).then(body=>{throw new Error(body&&body.message||`HTTP ${r.status}`);});
      return r.json();
    }).then(data=>{
      // S3 (Opus review): an error response's shape ({code,message}, from
      // job_api.send_error) has no `timepoints` array at all -- rendering
      // it as a successful survey threw a TypeError deep in routesScreen
      // (d.timepoints.length on undefined). Validate the shape here, not
      // just the HTTP status, before ever caching it as `routesData`.
      if(!data||!Array.isArray(data.timepoints))throw new Error((data&&data.message)||'想定外の応答でした。');
      routesData=data; routesLoading=false; if(screen==='routes')render();
    }).catch(error=>{
      // Deliberately NOT cached into routesData -- a transient failure
      // (server restart, network blip) must be retryable by revisiting the
      // tab, not stuck showing the same error forever.
      routesLoading=false;
      if(screen==='routes'){
        content.innerHTML=title('最短経路を調査')+`<p class="wp-muted">取得に失敗しました：${esc(error.message||String(error))}</p>`;
      }
    });
  }
  function routesScreen(){
    if(!routesData){loadRoutes();return title('最短経路を調査','主人公が結末へ至る段取りを計算しています…')+'<p class="wp-muted">計算中…</p>';}
    const d=routesData;
    if(d.status==='no_route_config'||d.status==='no_goal')
      return title('最短経路を調査')+`<p class="wp-muted">${esc(d.message)}</p>`;
    if(!d.timepoints.length)
      return title('最短経路を調査')+`<p class="wp-muted">${esc(d.message||'結末に到達する段取りが見つかりません')}</p>`;
    const tps=d.timepoints;
    const tp=tps[Math.min(routesTimepoint,tps.length-1)];
    const tabsHtml=tps.length>1?`<div class="wp-tabs" aria-label="時点の切り替え">${tps.map((t,i)=>`<button type="button" data-routes-tp="${i}" aria-pressed="${i===routesTimepoint}">${esc(t.label)}</button>`).join('')}</div>`:'';
    // S4 (Opus review): clarify what "#1" means (the engine's own cheapest
    // plan right now, not necessarily the shortest of the shown patterns
    // once a knockout route happens to finish faster) and that this is a
    // pre-play forecast (only scheduled events have been applied so far,
    // no actual decisions).
    const note='<p class="wp-muted">合理的に動いた場合の段取りです。#1はエンジンが今もっとも自然と判断した段取りで、必ずしも他より所要が短いとは限りません。主人公はまだ実際には動いていない前提（この時点までの予定イベントのみ適用）での見込みです。予定イベントは対象が死亡している／物語が先に終わっている場合は発火しません。日替わりイベントと乱数は含みません。所要は目安で、鍛錬などは1手として数えています。</p>';
    if(tp.blocked.length||!tp.routes.length){
      const reasons=(tp.blocked||[]).map(b=>`<li>${esc(b.text||JSON.stringify(b))}</li>`).join('');
      return title('最短経路を調査')+tabsHtml+`<p class="wp-muted">結末に到達する段取りが見つかりません</p>${reasons?`<ul>${reasons}</ul>`:''}`+note;
    }
    const routeSections=tp.routes.map(route=>`<section class="wp-section">${head(esc(route.label))}${route.conditions.length?`<p class="wp-muted">この段取りになる条件：${route.conditions.map(esc).join('・')}</p>`:''}<ol class="wp-steps">${route.steps.map(s=>`<li>${esc(s.text)}<small>累計 ${s.cumulative}</small></li>`).join('')}</ol>${route.truncated?'<p class="wp-muted">※途中で打ち切られました（手順が複雑すぎる可能性があります）。目安の所要：'+esc(route.h)+'</p>':''}</section>`).join('');
    const single=tp.routes.length<2?'<p class="wp-muted">別パターンは見つかりませんでした</p>':'';
    return title('最短経路を調査','主人公が結末へ至る、パターンの違う段取りです。')+tabsHtml+routeSections+single+note;
  }
  function render(focus=false){
    content.classList.toggle('is-people',screen==='people');
    content.dataset.screen=screen;
    content.innerHTML=({overview,people:peopleScreen,places:placesScreen,story,time:timeScreen,routes:routesScreen}[screen])();
    updateRelationTransform();
    root.querySelectorAll('.wp-nav [data-screen]').forEach(b=>{if(b.dataset.screen===screen)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
    const missing=[];
    if(!model.genre)missing.push('ジャンル');
    if(!people.length)missing.push('登場人物');
    if(!zones.length)missing.push('場所');
    if(!people.some(p=>p.id===w.protagonist))missing.push('主人公');
    if(!people.some(p=>p.id===w.antagonist))missing.push('敵役');
    if(!w.target_ending?.length)missing.push('目標の結末');
    if(people.some(p=>!p.range?.entry))missing.push('人物の初期位置');
    const next=root.querySelector('[data-next]');
    next.href=missing.length?`/worlds/${encodeURIComponent(model.id)}?view=advanced`:model.configUrl;
    next.textContent=missing.length?'未設定の項目を確認 →':'実行条件を決める →';
    if(model.editable)document.getElementById('wp-message').textContent=missing.length?'未設定：'+missing.join('・'):'編集した項目ごとに保存できます。';
    content.scrollTop=scrolls[screen]||0;
    if(focus)content.querySelector('h1').focus({preventScroll:true});
  }
  function openEditor(key,trigger){
    if (!model.editable || blocked) return;
    opener=trigger; changed=false;
    let name='',fields=[];
    const field=(key,label,value,type='textarea',options=null,required=false)=>({key,label,value,type,options,required});
    if(key==='overview'||key==='intro'){
      name=key==='overview'?'世界の説明を編集':'導入文を編集';fields=[...(key==='overview'?[field('name','世界の名前',model.name,'text')]:[]),field('text','文章',model[key])];
      applyEdit=values=>({operation:key,target:null,values});
    }else if(key==='genre'){
      name='ジャンルを選ぶ';
      fields=[field('template_id','ジャンル',model.genre||'','select',[['','選んでください'],...model.genres.map(g=>[g.id,g.name])],true)];
      applyEdit=values=>({operation:'genre',target:null,values});
    }else if(key==='time'){
      name='時間の範囲を編集';fields=[field('days','日数',w.time?.days||16,'number'),field('slots','時間帯（読点で区切る）',(w.time?.slots||[]).join('、'),'text')];
      applyEdit=v=>({operation:'time',target:null,values:{days:Number(v.days),slots:split(v.slots)}});
    }else if(key==='person'){
      const p=people[person];if(!p)return;
      name=`${p.id}を編集`;fields=[field('affiliation','所属（空欄なら開始時の居場所から表示）',p.affiliation||'','text'),field('description','人物の紹介',p.description||p.identity?.true||''),field('target','目標（手に入れたいものなど）',p.goal?.target||'','text'),field('deliver_to','届け先',p.goal?.deliver_to||'','select',zoneOptions(p.goal?.deliver_to)),field('personality','人物像',p.personality||'')];
      applyEdit=values=>({operation:'person',target:p.id,values});
    }else if(key==='relation'){
      const source=trigger.dataset.relationSource, target=trigger.dataset.relationTarget;
      const relation=people.find(p=>p.id===source)?.relations?.[target];if(!relation)return;
      name=`${source} → ${target} の関係ラベル`;fields=[field('label','関係ラベル（仲間・家族・敵対など／空欄で解除）',relation.label||'','text')];
      applyEdit=values=>({operation:'relation',target:{source,target},values});
    }else if(key==='place'){
      const z=zones[place];if(!z)return;name=`${z.name}を編集`;fields=[field('note','場所の説明',z.note||'')];applyEdit=values=>({operation:'place',target:z.name,values});
    }else if(key==='add-person'||key==='add-place'){
      name=key==='add-person'?'人物を追加':'場所を追加';fields=[field('name','名前','','text'),field('description','紹介・説明',''),...(key==='add-person'?[field('affiliation','所属（任意）','','text'),field('entry','開始時の居場所（あとで初期状態からも設定できます）','','select',zoneOptions(''))]:[])];
      applyEdit=values=>({operation:key,target:null,values});
    }else if(key==='state'){
      const p=people[person];if(!p)return;name=`${p.id}の開始時点の状態`;
      fields=[field('entry','居場所',p.range?.entry||'','select', zoneOptions(p.range?.entry)),field('knowledge','知識（読点で区切る）',(p.knowledge||[]).join('、'),'text')];
      applyEdit=v=>({operation:'state',target:p.id,values:{entry:v.entry,knowledge:split(v.knowledge)}});
    }else if(key.startsWith('route:')){
      const route=(w.routes?.[zones[place].name]||[])[Number(key.split(':')[1])];if(!route)return;
      name=`${zones[place].name} → ${route.to} の条件`;fields=[field('item','必要な持ち物（空欄なら条件なし）',route.requires_item||'','text'),field('cost','移動コスト',route.cost??1,'number')];
      applyEdit=v=>({operation:'route',target:{from:zones[place].name,index:Number(key.split(':')[1])},values:{item:v.item,cost:Number(v.cost)}});
    }else if(key==='add-event'||key.startsWith('event:')){
      const idx=key==='add-event'?null:Number(key.split(':')[1]);
      const ev=idx==null?{}:(w.scheduled_events||[])[idx];if(idx!=null&&!ev)return;
      name=idx==null?'出来事を追加':`${ev.label||ev.id}を編集`;
      const specs=model.force_action_specs||{};
      const verb=ev.force_action?.verb||'';
      const kinds=specs[verb]||[];
      const args=ev.force_action?.args||[];
      const currentTargets=ev.targets||[];
      const relevantVerbs=currentTargets.length
        ?Object.keys(specs).filter(v=>people.some(p=>currentTargets.includes(p.id)&&(p.verbs||[]).includes(v)))
        :Object.keys(specs);
      const verbOptions=[['','固定しない'],...relevantVerbs.sort().map(v=>[v,v]),...(verb&&!relevantVerbs.includes(verb)?[[verb,`${verb}（現在値・無効）`]]:[])];
      fields=[
        field('id','出来事のID',ev.id||'','text',null,true),
        field('label','名前',ev.label||'','text'),
        field('day','日',ev.day||1,'number'),
        field('slot','時間帯',ev.slot||'','select',[['','日の初め'],...(w.time?.slots||[]).map(s=>[s,s])]),
        field('targets','対象',currentTargets,'multiselect',people.map(p=>[p.id,p.id]),true),
        field('verb','行動を固定する（空欄なら固定しない）',verb,'select',verbOptions),
        {html:`<div id="wp-event-args">${kinds.map((kind,i)=>fieldHtml(argFieldDescriptor(kind,i,args[i]||''))).join('')}</div>`},
        {html:'<p class="wp-muted">後ろの引数を空にすると、その部分はエンジンがその時点で最も自然な候補を選びます。取れない行動だった場合、その時間帯は自由行動になります。</p>'},
        ...(ev.move_to?[{html:`<p class="wp-muted">行動を固定すると、瞬間移動（${esc(ev.move_to)}へ）は無効になります。</p>`}]:[]),
        field('item_name','得る持ち物（空欄なら付与しない）',ev.grants_item?.name||'','text'),
        field('item_count','個数',ev.grants_item?.count||1,'number'),
        field('stress_delta','ストレスの増減',ev.stress_delta||0,'number'),
      ];
      applyEdit=values=>({operation:idx==null?'add-event':'event',target:idx,values:{
        id:values.id,label:values.label,day:Number(values.day),slot:values.slot,
        targets:values.targets||[],verb:values.verb,
        args:Object.keys(values).filter(k=>/^arg\d+$/.test(k)).sort((a,b)=>Number(a.slice(3))-Number(b.slice(3))).map(k=>values[k]),
        item_name:values.item_name,item_count:Number(values.item_count)||1,stress_delta:Number(values.stress_delta)||0,
      }});
    }else if(key.startsWith('remove-event:')){
      const idx=Number(key.split(':')[1]);
      const ev=(w.scheduled_events||[])[idx];if(!ev)return;
      name=`「${ev.label||ev.id}」を削除しますか？`;fields=[];
      applyEdit=()=>({operation:'remove-event',target:idx,values:{}});
    }else return;
    const isRemove=key.startsWith('remove-event:');
    document.getElementById('wp-dialog-desc').textContent=isRemove
      ?'削除すると元に戻せません。過去の実行結果は変わりません。'
      :'保存すると世界設定を更新します。過去の実行結果は変わりません。';
    document.getElementById('wp-dialog-submit').textContent=isRemove?'削除する':'保存する';
    document.getElementById('wp-dialog-title').textContent=name;
    document.getElementById('wp-fields').innerHTML=fields.map(fieldHtml).join('')+'<p id="wp-edit-error" role="alert"></p>';
    document.getElementById('wp-field-verb')?.addEventListener('change',e=>{
      const newKinds=(model.force_action_specs||{})[e.target.value]||[];
      document.getElementById('wp-event-args').innerHTML=newKinds.map((kind,i)=>fieldHtml(argFieldDescriptor(kind,i,''))).join('');
    });
    dialog.showModal();
  }
  function numAttrs(key){
    const table={cost:['0.0001','any','10000'],day:['1','1',String(w.time?.days||10000)],
                 item_count:['1','1','999'],stress_delta:['-1000','any','1000']};
    const [min,step,max]=table[key]||['1','1','10000'];
    return `min="${min}" step="${step}" max="${max}" required`;
  }
  function fieldHtml(f){
    if(f.html!==undefined)return f.html;
    return `<label for="wp-field-${f.key}">${esc(f.label)}</label>`+(
      f.type==='textarea'?`<textarea id="wp-field-${f.key}" name="${f.key}">${esc(f.value)}</textarea>`:
      f.type==='select'?`<select id="wp-field-${f.key}" name="${f.key}" ${f.required?'required':''}>${f.options.map(([value,label])=>`<option value="${esc(value)}" ${value===f.value?'selected':''}>${esc(label)}</option>`).join('')}</select>`:
      f.type==='multiselect'?`<select id="wp-field-${f.key}" name="${f.key}" multiple ${f.required?'required':''}>${f.options.map(([value,label])=>`<option value="${esc(value)}" ${(f.value||[]).includes(value)?'selected':''}>${esc(label)}</option>`).join('')}</select>`:
      `<input id="wp-field-${f.key}" name="${f.key}" type="${f.type}" ${['affiliation','label'].includes(f.key)?'maxlength="120"':''} value="${esc(f.value)}" ${f.type==='number'?numAttrs(f.key):''}>`
    );
  }
  function withCurrentIfMissing(options,value){
    return value&&!options.some(([v])=>v===value)?[...options,[value,`${value}（現在値・無効）`]]:options;
  }
  function argFieldDescriptor(kind,i,value){
    const key='arg'+i;
    if(kind==='zone')return {key,label:`引数${i+1}（場所）`,value,type:'select',options:withCurrentIfMissing([['','（未指定）'],...zones.map(z=>[z.name,z.name])],value),required:false};
    if(kind==='subject')return {key,label:`引数${i+1}（人物）`,value,type:'select',options:withCurrentIfMissing([['','（未指定）'],...people.map(p=>[p.id,p.id])],value),required:false};
    if(kind.startsWith('enum:'))return {key,label:`引数${i+1}`,value,type:'select',options:withCurrentIfMissing([['','（未指定）'],...kind.slice(5).split('|').map(v=>[v,v])],value),required:false};
    return {key,label:`引数${i+1}`,value,type:'text',options:null,required:false};
  }
  root.addEventListener('click',e=>{
    const b=e.target.closest('button,[data-person]');if(!b)return;
    if(b.dataset.screen){scrolls[screen]=content.scrollTop;screen=b.dataset.screen;render(true);}
    else if(b.hasAttribute('data-event-day')){const day=Number(b.dataset.eventDay);eventDay=eventDay===day?null:day;render();}
    else if(b.hasAttribute('data-state-person')){person=Number(b.dataset.statePerson);openEditor('state',b);}
    else if(b.dataset.personLink){person=Number(b.dataset.personLink);screen='people';render(true);}
    else if(b.hasAttribute('data-person')){
      person=Number(b.dataset.person);root.querySelectorAll('[data-person]').forEach(el=>el.setAttribute('aria-pressed',String(Number(el.dataset.person)===person)));
      relationInspector=true;renderPeoplePanel();
    }
    else if(b.hasAttribute('data-rel-mode')){graphMode=b.dataset.relMode;relationZoom=1;relationPan={x:0,y:0};renderPeoplePanel();}
    else if(b.hasAttribute('data-rel-scope')){relationScope=b.dataset.relScope;relationZoom=1;relationPan={x:0,y:0};renderPeoplePanel();}
    else if(b.hasAttribute('data-rel-inspector')){relationInspector=b.dataset.relInspector==='open';renderPeoplePanel();}
    else if(b.hasAttribute('data-rel-zoom')){
      const svg=root.querySelector('.wp-rel-canvas'), view=svg.viewBox.baseVal, old=relationZoom;
      if(b.dataset.relZoom==='fit'){relationZoom=1;relationPan={x:0,y:0};}
      else {relationZoom=Math.max(.5,Math.min(3,relationZoom*(b.dataset.relZoom==='in'?1.25:.8)));const ratio=relationZoom/old;relationPan={x:view.width/2-(view.width/2-relationPan.x)*ratio,y:view.height/2-(view.height/2-relationPan.y)*ratio};}
      updateRelationTransform();
    }
    else if(b.hasAttribute('data-rel-fullscreen')){relationExpanded=!relationExpanded;renderPeoplePanel();root.querySelector('[data-rel-fullscreen]').focus();}
    else if(b.hasAttribute('data-place')){
      place=Number(b.dataset.place);root.querySelectorAll('[data-place]').forEach(el=>el.setAttribute('aria-pressed',String(Number(el.dataset.place)===place)));root.querySelector('.wp-place-detail').innerHTML=placeDetail();
    }else if(b.dataset.view){const [group,value]=b.dataset.view.split(':');if(group==='people')peopleView=value;if(group==='places')placeView=value;if(group==='story')storyView=value;render();root.querySelector(`[data-view="${b.dataset.view}"]`).focus();}
    else if(b.hasAttribute('data-routes-tp')){routesTimepoint=Number(b.dataset.routesTp);render();}
    else if(b.hasAttribute('data-relation-source'))openEditor('relation',b);
    else if(b.dataset.edit)openEditor(b.dataset.edit,b);
    else if(b.hasAttribute('data-route'))openEditor('route:'+b.dataset.route,b);
    else if(b.hasAttribute('data-close'))closeEditor();
  });
  root.addEventListener('change',e=>{
    const key=e.target.dataset.relSelect;if(!key)return;
    if(key==='focus'){const index=people.findIndex(p=>p.id===e.target.value);if(index>=0){person=index;graphMode='focus';relationInspector=true;}else graphMode='all';}
    if(key==='group')relationGroup=e.target.value;
    if(key==='kind')relationKind=e.target.value;
    relationZoom=1;relationPan={x:0,y:0};render();
  });
  root.addEventListener('keydown',e=>{if(e.key==='Escape'&&relationExpanded){e.preventDefault();relationExpanded=false;renderPeoplePanel();root.querySelector('[data-rel-fullscreen]')?.focus();return;}if(e.target.matches('.wp-rel-node')&&(e.key==='Enter'||e.key===' ')){e.preventDefault();e.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});
  root.addEventListener('pointerdown',e=>{
    const svg=e.target.closest('.wp-rel-canvas');if(!svg||e.target.closest('[data-person]')||e.button!==0||e.pointerType==='touch')return;
    const matrix=svg.getScreenCTM();if(!matrix)return;
    const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());
    relationDrag={id:e.pointerId,x:point.x,y:point.y,pan:{...relationPan}};svg.setPointerCapture(e.pointerId);
  });
  root.addEventListener('pointermove',e=>{
    if(!relationDrag||relationDrag.id!==e.pointerId)return;
    const svg=e.target.closest('.wp-rel-canvas');if(!svg)return;
    const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    relationPan={x:relationDrag.pan.x+point.x-relationDrag.x,y:relationDrag.pan.y+point.y-relationDrag.y};updateRelationTransform();
  });
  root.addEventListener('pointerup',()=>{relationDrag=null;});
  root.addEventListener('pointercancel',()=>{relationDrag=null;});
  root.addEventListener('input',e=>{
    if(!e.target.matches('.wp-search'))return;query=e.target.value;
    root.querySelectorAll('.wp-person-options [data-person]').forEach(b=>b.hidden=!people[Number(b.dataset.person)].id.includes(query));
    document.getElementById('wp-no-results').hidden=people.some(p=>p.id.includes(query));
  });
  const split = value => value.split(/[、,]/).map(s=>s.trim()).filter(Boolean);
  const zoneOptions = current => [['','未指定'],...zones.map(z=>[z.name,z.name]),...(current&&!zones.some(z=>z.name===current)?[[current,current+'（未登録）']]:[])];
  function closeEditor(){
    if(busy)return;
    if(changed&&!window.confirm('入力中の変更を破棄しますか？'))return;
    changed=false;dialog.close();
  }
  form.addEventListener('input',()=>{changed=true;});
  dialog.addEventListener('cancel',e=>{e.preventDefault();closeEditor();});
  form.addEventListener('submit',async e=>{
    e.preventDefault();if(busy||blocked||!form.reportValidity())return;
    const formData=new FormData(form);
    const formValues=Object.fromEntries(formData);
    form.querySelectorAll('select[multiple]').forEach(el=>{formValues[el.name]=formData.getAll(el.name);});
    const request=applyEdit(formValues);
    request.revision=model.revision;
    const errorBox=document.getElementById('wp-edit-error');
    busy=true;errorBox.textContent='保存中…';
    form.querySelectorAll('input,textarea,select,button').forEach(el=>el.disabled=true);
    let saved=null;
    try {
      const response=await fetch(`/api/worlds/${encodeURIComponent(model.id)}/edit`,{
        method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(request)
      });
      const result=await response.json();
      if(!response.ok){
        blocked=response.status===409||response.status>=500;
        errorBox.textContent=result.message||'保存できませんでした。入力内容を確認してください。';
        if(blocked)errorBox.append(' ',Object.assign(document.createElement('a'),{href:location.href,textContent:'再読み込みして確認'}));
      }else if(typeof result.revision!=='string'||!result.world||!Array.isArray(result.people)){
        throw new Error('invalid response');
      }else saved=result;
    }catch(error){
      blocked=true;
      errorBox.textContent='保存結果を確認できませんでした。再送せず、再読み込みして設定を確認してください。';
      errorBox.append(' ',Object.assign(document.createElement('a'),{href:location.href,textContent:'再読み込みして確認'}));
    }finally{
      busy=false;form.querySelectorAll('input,textarea,select,button').forEach(el=>el.disabled=false);
      form.querySelector('[type="submit"]').disabled=blocked;
    }
    if(!saved)return;
    const personId=request.operation==='add-person'?request.values.name.trim():people[person]?.id;
    const placeName=request.operation==='add-place'?request.values.name.trim():zones[place]?.name;
    Object.assign(model,saved);w=model.world;
    // genre isn't part of snapshot()'s model (execution/world_editor.py);
    // rederive it from gapengine.action_graph the same way LibraryStore._genre_of() does.
    const genreMatch=/^templates\/([A-Za-z0-9][A-Za-z0-9_-]{0,95})\//.exec(w.gapengine?.action_graph||'');
    model.genre=genreMatch?genreMatch[1]:null;
    people=model.people.filter(p=>p.id).sort((a,b)=>Number(b.id===w.protagonist)-Number(a.id===w.protagonist));
    zones=(w.zones||[]).map(z=>typeof z==='string'?{name:z,note:''}:{...z});
    person=Math.max(0,people.findIndex(p=>p.id===personId));place=Math.max(0,zones.findIndex(z=>z.name===placeName));
    if(request.operation==='add-person')query='';
    if(relationGroup&&!people.some(p=>WorldRelations.affiliation(p)===relationGroup))relationGroup='';
    changed=false;scrolls[screen]=content.scrollTop;const editKey=opener?.dataset.edit;dialog.close();render();
    const focus=request.operation==='relation'?Array.from(root.querySelectorAll('[data-relation-source]')).find(el=>el.dataset.relationSource===request.target.source&&el.dataset.relationTarget===request.target.target)||content.querySelector('h1'):root.querySelector(`[data-edit="${editKey}"]`)||content.querySelector('h1');
    focus?.focus({preventScroll:true});
    document.title=model.name+' | WorldBloom';
    const worldPicker=document.querySelector('[data-wb="world-picker"]');
    if(worldPicker?.selectedOptions[0])worldPicker.selectedOptions[0].textContent=model.name;
    document.getElementById('wp-message').textContent='保存しました。 '+document.getElementById('wp-message').textContent;
  });
  dialog.addEventListener('close',()=>{if(opener?.isConnected)opener.focus({preventScroll:true});});
  window.addEventListener('beforeunload',e=>{if(changed||busy){e.preventDefault();e.returnValue='';}});
  render();
})();
