/* Deliberately in-memory only: no fetch, storage or simulation mutations. */
(() => {
  'use strict';
  const root = document.querySelector('[data-world-prototype]');
  if (!root) return;
  const model = JSON.parse(document.getElementById('wp-data').textContent);
  const content = document.getElementById('wp-content');
  const dialog = root.querySelector('dialog');
  const form = document.getElementById('wp-form');
  const w = model.world;
  const people = model.people.filter(p => p.id);
  const zones = (w.zones || []).map(z => typeof z === 'string' ? {name:z,note:''} : {...z});
  people.sort((a,b) => Number(b.id === w.protagonist) - Number(a.id === w.protagonist));
  let screen = 'overview', person = 0, place = Math.max(0,zones.findIndex(z=>z.name==='海'));
  let peopleView = 'list', placeView = 'map', storyView = 'intro', query = '';
  let applyEdit = null, opener = null, changed = false;
  const scrolls = {};
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const absent = v => esc(v || '未入力');
  const items = p => Object.entries(p.inventory || {}).map(([k,v])=>`${k} × ${v}`).join('、') || 'なし';
  const knows = p => (p.knowledge || []).join('、') || 'なし';
  const entry = p => p.range?.entry || '未指定（初期化時に決定）';
  const goal = p => p?.previewGoal || (p?.goal?.target ? `${p.goal.target}${p.goal.deliver_to ? `を${p.goal.deliver_to}へ届ける` : 'を求める'}` : '未入力');
  const role = p => p.id === w.protagonist ? '主人公' : p.id === w.antagonist ? '敵役' : '';
  const edit = (key,label='編集') => `<button type="button" data-edit="${key}">✎ ${label}</button>`;
  const head = (title,action='') => `<div class="wp-section-head"><h2>${title}</h2>${action}</div>`;
  const title = (name,sub='',action='') => `<div class="wp-title"><h1 tabindex="-1">${esc(name)}</h1>${action}</div>${sub?`<p class="wp-subtitle">${esc(sub)}</p>`:''}`;
  const row = (name,value) => `<div><dt>${esc(name)}</dt><dd>${value}</dd></div>`;
  const tabs = (group,selected,entries) => `<div class="wp-tabs" aria-label="表示の切り替え">${entries.map(([key,label])=>`<button type="button" data-view="${group}:${key}" aria-pressed="${selected===key}">${label}</button>`).join('')}</div>`;
  const time = () => `${w.time?.days ?? '未指定'}日間 ・ ${(w.time?.slots||[]).join(' ／ ')}`;
  function overview() {
    const hero = people.find(p=>p.id===w.protagonist);
    return title(model.name,'この世界の前提を確認する') +
      `<section class="wp-section">${head('どんな世界？'+(model.overviewExample===false?'':'<span class="wp-example">プレビュー用の例文</span>'),edit('overview'))}<p>${absent(model.overview)}</p></section>`+
      `<div class="wp-columns"><section><h2>物語の出発点</h2><p>${hero?`${esc(hero.id)}は${esc(entry(hero))}にいる。`:'主人公は未指定です。'}</p></section><section><h2>目指す結末</h2><p>${esc(goal(hero))}</p></section></div>`+
      `<section class="wp-section">${head('この世界を構成するもの')}<button class="wp-jump" data-screen="people"><strong>登場人物</strong><span>${esc(people.map(p=>p.id).join('、')) || 'まだいません'}</span><span>→</span></button><button class="wp-jump" data-screen="places"><strong>場所</strong><span>${esc(zones.map(z=>z.name).join('、')) || 'まだありません'}</span><span>→</span></button><button class="wp-jump" data-screen="story"><strong>初期物語</strong><span>シミュレーション開始時点の導入・状況</span><span>→</span></button><button class="wp-jump" data-screen="time"><strong>時間</strong><span>${esc(time())}</span><span>→</span></button></section>`+
      `<details class="wp-section"><summary>詳しい世界設定</summary><p>状況別の定石・行動図鑑・設定ファイルは、<a href="/worlds/${encodeURIComponent(model.id)}">現在の世界設定画面</a>で確認できます。</p></details>`;
  }
  function personDetail() {
    const p = people[person];
    if (!p) return '<p>人物を追加すると、ここに詳細が表示されます。</p>';
    const relations = Object.entries(p.relations||{}).map(([name,r])=>`<span>${esc(name)} <small class="wp-muted">${r.affinity>0?'好意がある':r.affinity<0?'反感がある':'中立・未設定'}</small></span>`).join('') || '関係は未設定です。';
    return `<div class="wp-section-head"><span class="wp-role">${esc(role(p)||'登場人物')}</span>${edit('person','人物を編集')}</div><h2>${esc(p.id)}</h2><p>${absent(p.description || p.identity?.true || p.identity?.['true'])}</p><dl class="wp-dl">${row('目的',esc(p.previewGoal || goal(p)))}${row('人物像',absent(p.personality))}${row('ほかの人物との関係',`<div class="wp-relation-list">${relations}</div>`)}${row('持ち物・知っていること',`${esc(items(p))}<br>${esc(knows(p))}`)}</dl><details><summary>性格・能力の数値</summary><p>${esc(Object.entries(p.traits||{}).map(([k,v])=>`${k}: ${v}`).join(' ／ ')) || '未設定'}<br>基礎値: ${esc(p.base ?? '未設定')}</p></details><details><summary>秘密・他者からの見え方</summary><p>本当の姿: ${absent(p.identity?.true)}<br>表向きの姿: ${absent(p.identity?.displayed)}</p></details>`;
  }
  function personButton(p,i){return `<button class="wp-person-button" data-person="${i}" aria-pressed="${i===person}" ${!p.id.includes(query)?'hidden':''}>${esc(p.id)}<small>${esc(role(p))}</small></button>`;}
  function peopleScreen() {
    const top = title('登場人物','',`<span>${people.length}人</span><button class="wp-add" data-edit="add-person">＋ 人物を追加</button>`);
    return top + `<div class="wp-people"><aside class="wp-people-list"><input class="wp-search" type="search" aria-label="人物を探す" placeholder="人物を探す" value="${esc(query)}">${tabs('people',peopleView,[['list','一覧'],['relations','相関図']])}<div class="wp-person-options">${people.map(personButton).join('')}</div><p class="wp-muted" id="wp-no-results" ${people.some(p=>p.id.includes(query))?'hidden':''}>一致する人物がいません。</p></aside><article class="wp-person-detail">${peopleView==='relations'?relationsView():personDetail()}</article></div>`;
  }
  function relationsView(){
    const p=people[person]; if(!p)return '<p>人物がいません。</p>';
    const others=Object.entries(p.relations||{});
    const points=others.map((_,i)=>[50+36*Math.cos(i/others.length*2*Math.PI),50+35*Math.sin(i/others.length*2*Math.PI)]);
    const graph=`<div class="wp-map wp-relations"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${points.map(pt=>`<line x1="50" y1="50" x2="${pt[0]}" y2="${pt[1]}"/>`).join('')}</svg><span class="wp-graph-center">${esc(p.id)}</span>${others.map(([name],i)=>`<button style="left:${points[i][0]}%;top:${points[i][1]}%" data-person="${people.findIndex(p=>p.id===name)}" ${people.some(p=>p.id===name)?'':'disabled'}>${esc(name)}</button>`).join('')}</div>`;
    return `<h2>${esc(p.id)}から見た関係</h2><p class="wp-muted">中心の人物から相手への関係です。相手の気持ちとは異なる場合があります。</p>${graph}<details><summary>関係を数値で確認</summary>` + others.map(([name,r])=>{
      const i=people.findIndex(p=>p.id===name);
      return `<button class="wp-route" ${i>=0?`data-person="${i}"`:'disabled'}>${esc(p.id)} → ${esc(name)}<small>親しさ ${esc(r.affinity??'未設定')} ／ 認知 ${esc(r.awareness??'未設定')}</small></button>`;
    }).join('')+'</details>';
  }
  const routeLabel = r => `${r.requires_item ? `${r.requires_item}が必要` : '持ち物の条件なし'}${r.cost != null ? ` ・ コスト ${r.cost}` : ''}`;
  function placeDetail(){
    const z=zones[place];if(!z)return '<p>場所を追加すると詳細を表示します。</p>';
    const routes = w.routes?.[z.name] || [];
    const activities = (w.items||[]).filter(i=>i.craft_zone===z.name).map(i=>`${i.name}を作る`);
    return `<p class="wp-muted">選択した場所</p>${head(esc(z.name),edit('place'))}<p>${absent(z.note)}</p><section class="wp-section">${head('ここでできること')}<p>${esc(activities.join('、')||'製作するものの指定はありません。')}</p></section><section class="wp-section">${head('ここから移動できる場所')}${routes.map((r,i)=>`<button class="wp-route" data-route="${i}">${esc(z.name)} → ${esc(r.to)}<small>${esc(routeLabel(r))}</small></button>`).join('')||'<p>経路は未設定です。</p>'}</section><p class="wp-muted">往路の条件です。復路は相手の場所を選んで確認できます。</p>`;
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
  function placesScreen(){return title('場所','この世界に登場する場所と、つながりを整理しましょう。',`<span>${zones.length}か所</span><button class="wp-add" data-edit="add-place">＋ 場所を追加</button>`)+tabs('places',placeView,[['map','つながり'],['list','一覧']])+`<div class="wp-place-grid"><div>${placeView==='map'?map():`<div class="wp-list">${zones.map((z,i)=>`<button data-place="${i}" aria-pressed="${i===place}">${esc(z.name)}</button>`).join('')}</div>`}<p class="wp-muted">場所を選ぶと詳細を表示します。移動条件は右の経路から確認できます。</p></div><article class="wp-place-detail">${placeDetail()}</article></div>`;}
  function stateTable(){return `<p class="wp-muted">設定ファイルの初期値です。開始時イベント・乱数による変化は実行時に決まります。</p><table class="wp-state"><thead><tr><th>人物</th><th>居場所</th><th>持ち物</th><th>知識</th></tr></thead><tbody>${people.map((p,i)=>`<tr><th><button data-person-link="${i}">${esc(p.id)} ↗</button></th><td>${esc(entry(p))}</td><td>${esc(items(p))}</td><td>${esc(knows(p))}</td></tr>`).join('')}</tbody></table>`;}
  function story(){return title('初期物語','シミュレーションが始まる直前の状況')+tabs('story',storyView,[['intro','導入文'],['state','開始時点の状態']])+(storyView==='intro'?`<section class="wp-section">${head('旅立ちの前'+(model.introExample===false?'':'<span class="wp-example">プレビュー用の例文</span>'),edit('intro','導入文を編集'))}<p class="wp-story-copy">${absent(model.intro)}</p></section><button class="wp-route" data-view="story:state">開始時点の状態を確認 →</button>`:`<section class="wp-section">${head('開始時点の状況',edit('state','初期状態を編集'))}${stateTable()}</section>`)+`<p class="wp-muted wp-section">この先の展開は、シミュレーションで決まります。導入文を編集しても開始状態は変わりません。</p>`;}
  function timeScreen(){
    const days=Number(w.time?.days)||0;
    const slots=w.time?.slots||[];
    const cycle=slots.length?slots:['時間帯は未設定'];
    return title('時間','シミュレーションの長さと、1日の進み方',edit('time','時間を編集'))+
      `<div class="wp-time-summary"><section><span class="wp-time-label">物語の期間</span><strong>${days?`${esc(days)}日間`:'未設定'}</strong><p>この期間の中で、人物が行動し物語が進みます。</p></section><section><span class="wp-time-label">1日の区切り</span><strong>${slots.length?`${esc(slots.length)}区切り`:'未設定'}</strong><p>${slots.length?'1日の中を複数の時間帯に分けて進めます。':'時間帯を設定してください。'}</p></section></div>`+
      `<section class="wp-section">${head('1日の流れ')}<ol class="wp-time-cycle">${cycle.map((slot,index)=>`<li><span>${index+1}</span><strong>${esc(slot)}</strong>${index<cycle.length-1?'<i aria-hidden="true">→</i>':''}</li>`).join('')}</ol><p class="wp-muted">最後の時間帯が終わると、次の日の最初の時間帯へ進みます。</p></section>`+
      `<section class="wp-section">${head('設定内容')}<dl class="wp-dl">${row('日数',days?`${esc(days)}日間`:'未設定')}${row('時間帯',esc(slots.join(' ／ ')||'未設定'))}</dl></section>`;
  }
  function render(focus=false){
    content.classList.toggle('is-people',screen==='people');
    content.dataset.screen=screen;
    content.innerHTML=({overview,people:peopleScreen,places:placesScreen,story,time:timeScreen}[screen])();
    root.querySelectorAll('.wp-nav [data-screen]').forEach(b=>{if(b.dataset.screen===screen)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
    content.scrollTop=scrolls[screen]||0;
    if(focus)content.querySelector('h1').focus({preventScroll:true});
  }
  function openEditor(key,trigger){
    opener=trigger;
    let name='',fields=[];
    const field=(key,label,value,type='textarea',options=null)=>({key,label,value,type,options});
    if(key==='overview'||key==='intro'){
      name=key==='overview'?'世界の説明を編集':'導入文を編集';fields=[field('text','文章',model[key])];
      applyEdit=values=>{model[key]=values.text;model[key+'Example']=false;};
    }else if(key==='time'){
      name='時間の範囲を編集';fields=[field('days','日数',w.time?.days||16,'number'),field('slots','時間帯（読点で区切る）',(w.time?.slots||[]).join('、'),'text')];
      applyEdit=v=>{w.time={...w.time,days:Number(v.days),slots:v.slots.split(/[、,]/).map(s=>s.trim()).filter(Boolean)};};
    }else if(key==='person'){
      const p=people[person];if(!p)return;
      name=`${p.id}を編集`;fields=[field('description','人物の紹介',p.description||p.identity?.true||''),field('goal','目的',p.previewGoal||goal(p)),field('personality','人物像',p.personality||'')];
      applyEdit=v=>{p.description=v.description;p.previewGoal=v.goal;p.personality=v.personality;};
    }else if(key==='place'){
      const z=zones[place];if(!z)return;name=`${z.name}を編集`;fields=[field('note','場所の説明',z.note||'')];applyEdit=v=>{z.note=v.note;};
    }else if(key==='add-person'||key==='add-place'){
      name=key==='add-person'?'人物を追加':'場所を追加';fields=[field('name','名前','','text'),field('description','紹介・説明','')];
      applyEdit=v=>{
        const all=key==='add-person'?people.map(p=>p.id):zones.map(z=>z.name);
        if(!v.name.trim()||all.includes(v.name.trim()))return '空欄・同じ名前では追加できません。';
        if(key==='add-person'){people.push({id:v.name.trim(),description:v.description});person=people.length-1;query='';}
        else{zones.push({name:v.name.trim(),note:v.description});place=zones.length-1;}
      };
    }else if(key==='state'){
      const p=people[person];if(!p)return;name=`${p.id}の開始時点の状態`;
      fields=[field('entry','居場所',p.range?.entry||'','select', [['','未指定'],...zones.map(z=>[z.name,z.name])]),field('knowledge','知識（読点で区切る）',(p.knowledge||[]).join('、'),'text')];
      applyEdit=v=>{p.range={...p.range,entry:v.entry};p.knowledge=v.knowledge.split(/[、,]/).map(s=>s.trim()).filter(Boolean);};
    }else if(key.startsWith('route:')){
      const route=(w.routes?.[zones[place].name]||[])[Number(key.split(':')[1])];if(!route)return;
      name=`${zones[place].name} → ${route.to} の条件`;fields=[field('item','必要な持ち物（空欄なら条件なし）',route.requires_item||'','text'),field('cost','移動コスト',route.cost??1,'number')];
      applyEdit=v=>{route.requires_item=v.item;route.cost=Number(v.cost);};
    }else return;
    document.getElementById('wp-dialog-title').textContent=name;
    document.getElementById('wp-fields').innerHTML=fields.map(f=>`<label for="wp-field-${f.key}">${esc(f.label)}</label>`+(f.type==='textarea'?`<textarea id="wp-field-${f.key}" name="${f.key}">${esc(f.value)}</textarea>`:f.type==='select'?`<select id="wp-field-${f.key}" name="${f.key}">${f.options.map(([value,label])=>`<option value="${esc(value)}" ${value===f.value?'selected':''}>${esc(label)}</option>`).join('')}</select>`:`<input id="wp-field-${f.key}" name="${f.key}" type="${f.type}" value="${esc(f.value)}" ${f.type==='number'?'min="1" max="10000" required':''}>`)).join('')+'<p id="wp-edit-error" role="alert"></p>';
    dialog.showModal();
  }
  root.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.hasAttribute('data-reset')){changed=false;location.reload();return;}
    if(b.dataset.screen){scrolls[screen]=content.scrollTop;screen=b.dataset.screen;render(true);}
    else if(b.dataset.personLink){person=Number(b.dataset.personLink);screen='people';render(true);}
    else if(b.hasAttribute('data-person')){
      person=Number(b.dataset.person);root.querySelectorAll('[data-person]').forEach(el=>el.setAttribute('aria-pressed',String(Number(el.dataset.person)===person)));
      const panel=root.querySelector('.wp-person-detail');panel.innerHTML=peopleView==='relations'?relationsView():personDetail();panel.scrollTop=0;
    }else if(b.hasAttribute('data-place')){
      place=Number(b.dataset.place);root.querySelectorAll('[data-place]').forEach(el=>el.setAttribute('aria-pressed',String(Number(el.dataset.place)===place)));root.querySelector('.wp-place-detail').innerHTML=placeDetail();
    }else if(b.dataset.view){const [group,value]=b.dataset.view.split(':');if(group==='people')peopleView=value;if(group==='places')placeView=value;if(group==='story')storyView=value;render();root.querySelector(`[data-view="${b.dataset.view}"]`).focus();}
    else if(b.dataset.edit)openEditor(b.dataset.edit,b);
    else if(b.hasAttribute('data-route'))openEditor('route:'+b.dataset.route,b);
    else if(b.hasAttribute('data-close'))dialog.close();
  });
  root.addEventListener('input',e=>{
    if(!e.target.matches('.wp-search'))return;query=e.target.value;
    root.querySelectorAll('.wp-person-options [data-person]').forEach(b=>b.hidden=!people[Number(b.dataset.person)].id.includes(query));
    document.getElementById('wp-no-results').hidden=people.some(p=>p.id.includes(query));
  });
  form.addEventListener('submit',e=>{
    e.preventDefault();const error=applyEdit(Object.fromEntries(new FormData(form)));
    if(error){document.getElementById('wp-edit-error').textContent=error;return;}
    changed=true;scrolls[screen]=content.scrollTop;dialog.close();const editKey=opener?.dataset.edit;render();
    root.querySelector(`[data-edit="${editKey}"]`)?.focus({preventScroll:true});
    document.getElementById('wp-message').textContent='画面内に反映しました。元の設定には保存されません。';
  });
  dialog.addEventListener('close',()=>{if(opener?.isConnected)opener.focus({preventScroll:true});});
  window.addEventListener('beforeunload',e=>{if(changed){e.preventDefault();e.returnValue='';}});
  render();
})();
