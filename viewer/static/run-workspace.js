"use strict";
(() => {
  const root = document.querySelector("[data-run-workspace]");
  if (!root) return;
  let payload = JSON.parse(root.dataset.initial);
  let observed = payload.observation;
  let job = payload.job;
  const terminal = new Set(payload.terminal_states);
  const $ = (selector) => root.querySelector(selector);
  const $$ = (selector) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const number = value => typeof value === "number" && Number.isFinite(value);
  const fmt = value => number(value) ? value.toLocaleString("ja-JP", {maximumFractionDigits: 2}) : "—";
  const percent = value => number(value) ? `${Math.round(value * 100)}%` : "—";
  const duration = seconds => number(seconds) ? `${Math.floor(seconds / 60)}分${String(Math.floor(seconds % 60)).padStart(2,"0")}秒` : "計測中";
  const key = `worldbloom:run-observer:${job.run_id || observed.run_name || location.pathname}`;
  let params = new URL(location.href).searchParams;
  const readSaved = () => {try {return JSON.parse(sessionStorage.getItem(key) || "{}");} catch (_) {return {};}};
  const saved = readSaved();
  const tabNames = ["overview","replay","river","trends"];
  let tab = params.get("tab") || saved.tab || "overview";
  if (!tabNames.includes(tab)) tab = "overview";
  let follow = !params.has("gen");
  let selectedNode = params.get("node") || "";
  let metric = params.get("metric") || "occupied_cells";
  if (!["occupied_cells","reach_rate","average_archive_quality"].includes(metric)) metric = "occupied_cells";
  let trendGen = observed.generation;
  let rangeMode = "window";
  let rangeEnd = observed.generation;
  let lastConfirmed = "";
  let requestSequence = 0;
  let manualRequest = 0;
  let playbackSpeed = 1;
  let playControl = null;
  let timer;
  let stoppingRequested = job.state === "stopping";
  let pendingRevision = Number(job.publication_revision || 0);
  const replay = () => $("[data-replay-host] .ga-replay");
  const empty = text => `<p class="rw-empty">${esc(text)}</p>`;
  function remember(push=false) {
    const url = new URL(location.href);
    if (job.job_id) url.pathname = `/jobs/${encodeURIComponent(job.job_id)}`;
    url.searchParams.set("tab", tab);
    if (!follow && number(observed.generation)) url.searchParams.set("gen", observed.generation);
    else url.searchParams.delete("gen");
    if (selectedNode) url.searchParams.set("node", selectedNode); else url.searchParams.delete("node");
    if (tab === "trends") url.searchParams.set("metric", metric); else url.searchParams.delete("metric");
    url.searchParams.delete("view-data");
    history[push ? "pushState" : "replaceState"]({}, "", url);
    try {sessionStorage.setItem(key, JSON.stringify({tab}));} catch (_) { /* optional UI memory */ }
  }
  function switchTab(next, push=true) {
    if (tab === "replay" && next !== "replay") replay()?.wbPause?.();
    tab = next;
    $$("[data-tab]").forEach(button => {
      const active = button.dataset.tab === tab;
      button.setAttribute("aria-selected", active); button.tabIndex = active ? 0 : -1;
      $(`#rw-${button.dataset.tab}`).hidden = !active;
    });
    $(".rw-content").scrollTop = 0;
    remember(push);
    requestAnimationFrame(fitCharts);
    if (follow && next !== "replay" && observed.generation < observed.latest) loadObservation(null);
  }
  $$("[data-tab]").forEach(button => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
    button.addEventListener("keydown", event => {
      let i = tabNames.indexOf(button.dataset.tab);
      if (event.key === "ArrowRight") i = (i+1)%4;
      else if (event.key === "ArrowLeft") i = (i+3)%4;
      else if (event.key === "Home") i = 0;
      else if (event.key === "End") i = 3;
      else return;
      event.preventDefault(); switchTab(tabNames[i]); $(`[data-tab="${tabNames[i]}"]`).focus();
    });
  });
  function jobState(next) {
    job = next;
    const p = job.progress || {};
    const ended = terminal.has(job.state) || !job.job_id;
    $("[data-run-title]").textContent = ended ? "実行結果" : "実行状況";
    $("[data-status]").textContent = payload.state_labels[job.state] || job.state || "記録";
    $("[data-status]").className = `state-badge state-${job.state}`;
    let phase = payload.phase_labels[job.phase] || "準備中";
    if (job.state === "running" && job.phase === "evaluating") phase = `第${(p.completed_generations || 0)+1}世代を計算中`;
    if (job.state !== "running") phase = payload.state_labels[job.state] || phase;
    $("[data-phase]").textContent = phase;
    $("[data-progress]").textContent = `${fmt(p.completed_generations)} / ${fmt(p.total_generations)} 世代完了`;
    const bar = $("progress");
    if (number(p.total_generations) && p.total_generations > 0 && number(p.completed_generations)) {
      bar.max=p.total_generations; bar.value=p.completed_generations;
    } else bar.removeAttribute("value");
    let elapsed = p.elapsed_seconds;
    if (!number(elapsed) && number(job.started_at)) {
      const end = job.finished_at || job.updated_at;
      if (number(end)) elapsed = Math.max(0,end-job.started_at);
    }
    $("[data-elapsed]").textContent = `経過 ${duration(elapsed)}`;
    $("[data-stop]").hidden = ended;
    $("[data-restart]").hidden = !ended;
    if (job.state === "stopping") stoppingRequested = true;
    $("[data-stop]").disabled = stoppingRequested;
    $("[data-stop]").textContent = stoppingRequested ? "停止処理中" : "実行を停止";
    let eta = "計測中";
    if (ended) eta = "終了";
    else if (number(elapsed) && p.completed_seeds > 0 && p.total_seeds >= p.completed_seeds)
      eta = `約${Math.max(1,Math.ceil(elapsed / p.completed_seeds * (p.total_seeds-p.completed_seeds) / 60))}分`;
    $("[data-overview-progress]").innerHTML = `<div class="rw-stats"><div><span>評価済み（seed別）</span><strong>${fmt(p.completed_seeds)} / ${fmt(p.total_seeds)} 回</strong></div><div><span>残り時間の目安</span><strong>${eta}</strong></div></div>`;
    const errorCode = typeof job.error === "object" ? job.error?.code : job.error;
    const message = payload.error_messages[errorCode] || [errorCode ? `エラー: ${errorCode}` : "実行が中断しました。", "実行ログを確認してください。"];
    const stamp = value => {const d = new Date(typeof value === "number" ? value*1000 : value);return value && !Number.isNaN(d.getTime()) ? d.toLocaleString("ja-JP") : "—";};
    const dates = `<p>開始：${esc(stamp(job.started_at))}／終了：${esc(stamp(job.finished_at))}</p>`;
    const errors = job.error ? `<p class="rw-error">${esc(message[0])}</p><p>${esc(message[1])}</p>` : "";
    $("[data-terminal-message]").innerHTML = job.state === "cancelled" && !job.error ? '<p>利用者の停止要求により停止しました。同じ設定で新しく実行できます。</p>' : errors || (job.state === "interrupted" ? '<p>実行が中断しました。保存済みの結果を確認してから、同じ条件で新しく実行できます。</p>' : "");
    $("[data-run-log]").innerHTML = `${errors}${dates}<p>状態：${esc(payload.state_labels[job.state])}／${esc(phase)}</p><p>評価済み個体：${fmt(p.completed_individuals)} / ${fmt(p.total_individuals)}</p><p>この画面はGA探索の進捗です。文章生成は別の工程です。</p>`;
    const action = $("[data-candidates]");
    if (observed.candidate_count > 0 && job.run_id) {
      action.href = `/runs/${encodeURIComponent(job.run_id)}/candidates`;
      action.textContent = "保存済みの候補を見る →"; action.removeAttribute("aria-disabled");
    } else if (!job.job_id && observed.run_name && Object.keys(observed.cells).length) {
      action.href = `/exp/${encodeURIComponent(observed.run_name)}`;
      action.textContent = "保存済みの候補を見る →"; action.removeAttribute("aria-disabled");
    } else if (ended) {
      action.href = $("[data-restart]").href;
      action.textContent = "条件を見直す →"; action.removeAttribute("aria-disabled");
    } else {
      action.removeAttribute("href"); action.textContent = "候補の保存を待っています"; action.setAttribute("aria-disabled","true");
    }
  }
  const transportIcon = name => `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">${name === "prev" ? '<path d="M6 5v14M19 5l-9 7 9 7z"/>' : name === "next" ? '<path d="M18 5v14M5 5l9 7-9 7z"/>' : '<path d="M8 5l11 7-11 7z"/>'}</svg>`;
  function attachPlayControl() {
    const slot = $("[data-play-slot]");
    if (slot && playControl) slot.replaceChildren(playControl);
  }
  function generationControls() {
    $$("[data-generation-controls]").forEach(host => {
      const disabled = !observed.historical;
      const options = Array.from({length:(observed.latest ?? -1)+1},(_,g) => `<option value="${g}" ${g===observed.generation?"selected":""}>第${g+1}世代</option>`).join("");
      const transport = host.dataset.generationControls === "replay";
      const locked = disabled || !!manualRequest;
      host.innerHTML = `<div class="rw-generation rw-transport" role="group" aria-label="${transport?"再生と世代の操作":"表示世代の操作"}">
        <div class="rw-transport-buttons">
          <button class="rw-icon-button" data-prev-generation aria-label="前の世代へ" title="前の世代へ" ${locked||!(observed.generation>0)?"disabled":""}>${transportIcon("prev")}</button>
          ${transport?`<span data-play-slot><button class="rw-icon-button" disabled aria-label="再生" title="再生できる記録がありません">${transportIcon("play")}</button></span>`:""}
          <button class="rw-icon-button" data-next-generation aria-label="次の世代へ" title="次の世代へ" ${locked||!(observed.generation<observed.latest)?"disabled":""}>${transportIcon("next")}</button>
          ${transport?`<select class="rw-speed" data-speed aria-label="再生速度" title="再生速度" ${observed.replay?"":"disabled"}>${[.5,1,2,4].map(v=>`<option value="${v}" ${playbackSpeed===v?"selected":""}>${v}×</option>`).join("")}</select>`:""}
        </div>
        <select data-generation aria-label="表示する世代" title="表示する世代" ${locked?"disabled":""}>${options||'<option>記録待ち</option>'}</select>
        <span class="rw-generation-total">/ ${observed.latest===null?"—":observed.latest+1} 世代</span>
        <button class="rw-live-button ${follow?"is-following":""}" data-go-live aria-label="最新の世代を追う" aria-pressed="${follow}" title="${follow?"最新の保存世代を追従中":"最新の保存世代へ戻って追従する"}" ${disabled?"disabled":""}><span class="rw-live-dot" aria-hidden="true"></span>LIVE</button>
      </div>`;
    });
    attachPlayControl();
  }
  function overview() {
    const rationalityHost = $("[data-rationality]");
    if (rationalityHost) rationalityHost.innerHTML = payload.rationality_html || "";
    const gen = (observed.generations || []).find(g=>g.generation===observed.generation) || {};
    const total = observed.categories.length * observed.bins.length;
    $("[data-overview-metrics]").innerHTML = `<div class="rw-stats"><div><span>物語の種類</span><strong>${Object.keys(observed.cells).length} / ${total}</strong></div><div><span>${number(observed.generation)?`第${observed.generation+1}世代の`:""}結末到達率</span><strong>${percent(gen.reach_rate)}</strong></div></div>`;
    const heading = `<tr><th>行動の傾向＼起伏</th>${observed.bins.map(b=>`<th>${esc(b)}</th>`).join("")}</tr>`;
    const rows = observed.categories.map(c=>`<tr><th scope="row">${esc(c)}</th>${observed.bins.map(b=>{
      const cell = observed.cells[`${c}|${b}`]; const value=cell?.quality;
      return `<td class="${cell?"is-filled":"is-empty"}"><span>${cell?(number(value)?fmt(value):"品質未計測"):"空き"}</span></td>`;
    }).join("")}</tr>`).join("");
    const recent = observed.generations.slice(-3).reverse();
    $("[data-recent-saves]").innerHTML = recent.length ? `<h3>直近の保存記録</h3><ul>${recent.map(g=>`<li>第${g.generation+1}世代を保存 · ${fmt(g.occupied_cells)}種類 · 結末到達率 ${percent(g.reach_rate)}</li>`).join("")}</ul>` : "";
    $("[data-map]").innerHTML = `<h3>物語の地図</h3><table class="rw-map"><thead>${heading}</thead><tbody>${rows}</tbody></table><p class="muted">数値は地図に残った代表の品質です。${number(observed.generation)?`第${observed.generation+1}世代時点。`:esc(observed.notice)}</p>`;
  }
  function mountReplay() {
    window.WorldBloomReplay?.stop();
    const host = $("[data-replay-host]");
    if (!observed.replay_html) {playControl=null;host.innerHTML=empty(observed.notice || "この実行にはリプレイ用の記録がありません。");return;}
    host.innerHTML = observed.replay_html;
    const note=$("#rw-replay > p");
    if(note) note.remove();
    const guide=document.createElement("p");
    guide.className="rw-replay-guide";
    guide.textContent="保存済みの世代を再生しています。計算の進捗は上部で確認できます。";
    host.querySelector(".ga-replay-block").append(guide);
    const inner = replay(); inner.dataset.rwManaged="true"; inner.dataset.active=String(tab==="replay");
    window.WorldBloomReplay.mount(inner);
    playControl = host.querySelector(".ga-replay-player-btn");
    attachPlayControl();
    inner.wbSpeed(playbackSpeed);
    host.querySelectorAll(".ga-replay-gen").forEach((a,g)=>a.href=`${location.pathname}?tab=replay&gen=${g}`);
  }
  const svg = (tag,attrs={},text) => {const el=document.createElementNS("http://www.w3.org/2000/svg",tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));if(text!==undefined)el.textContent=text;return el;};
  function river() {
    const host=$("[data-river-host]"); const model=observed.river;
    const tableOpen=host.querySelector("details")?.open || false;
    if(!model){host.innerHTML=empty(observed.notice || "この実行には系譜用の世代記録がありません。");return;}
    const last=observed.generation ?? model.counts.generations-1;
    rangeEnd=Math.min(last,rangeEnd??last);
    const count=innerWidth<768?3:7;
    const start=rangeMode==="all"?0:Math.max(0,rangeEnd-count+1);
    const end=rangeMode==="all"?last:rangeEnd;
    $("[data-range]").value=rangeMode;
    $("[data-range-prev]").disabled=rangeMode==="all"||start===0;
    $("[data-range-next]").disabled=rangeMode==="all"||end===last;
    const chosen=model.nodes.find(n=>n.id===selectedNode);
    const highlighted=chosen?.elite || null;
    const current=job.job_id && !terminal.has(job.state) || observed.generation !== observed.latest;
    const label=current?"この時点で残った血筋":"最後に残った血筋";
    const cols=end-start+1, W=Math.max(520, $(".rw-content").clientWidth-290, rangeMode==="all" ? 190+cols*60 : 0), left=145, right=45;
    const x=g=>left+(g-start)*(W-left-right)/Math.max(1,cols-1);
    const rawBands=model.bands.filter(b=>model.nodes.some(n=>n.position?.band===b.key && n.generation>=start && n.generation<=end));
    const pos=new Map();
    let y=32;
    const bands=rawBands.map(b=>{
      const nodes=model.nodes.filter(n=>n.position?.band===b.key && n.generation>=start && n.generation<=end);
      const maxStack=Math.max(1,...Array.from({length:cols},(_,i)=>nodes.filter(n=>n.generation===start+i).length));
      const h=Math.max(22,maxStack*8+8), cy=y+h/2;
      for(let g=start;g<=end;g++)nodes.filter(n=>n.generation===g).forEach((n,i)=>pos.set(n.id,{x:x(g),y:y+8+i*8}));
      const out={...b,cy,top:y,h};y+=h;return out;
    });
    const H=y+76;
    host.innerHTML=`<p>${fmt(model.counts.total)}個体を記録・地図に残った型 ${fmt(model.counts.elites)}・親を特定できない参照 ${fmt(model.counts.unresolved)}本</p><div class="rw-river-layout"><div data-river-canvas></div><aside class="rw-node-info" data-node-info></aside></div><p class="rw-legend"><b>━━ ${label}</b>　┄┄ その他の親子　● 点の濃さ＝品質　○ 品質未計測 · 記録のある型を表示</p><details><summary>個体を表で確認</summary><div class="rw-table-wrap" data-node-table></div></details>`;
    host.querySelector("details").open=tableOpen;
    const chart=svg("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":`第${start+1}〜${end+1}世代の系譜。${label}`});
    chart.style.minHeight=`${H}px`;
    chart.style.minWidth=`${rangeMode === "all" ? Math.max(W, 190+cols*60) : W}px`;
    for(let g=start;g<=end;g++)chart.append(svg("text",{x:x(g),y:22,"text-anchor":"middle"},`第${g+1}世代`));
    bands.forEach(b=>{
      chart.append(svg("line",{x1:left-15,x2:W-right+15,y1:b.cy,y2:b.cy,class:"rw-grid-line"}));
      const text=b.key==="__offmap_parent__"?"地図外の親":b.label;
      chart.append(svg("text",{x:8,y:b.cy+4,class:"rw-band-label"},text));
    });
    const nodesById=new Map(model.nodes.map(n=>[n.id,n]));
    let omitted=0;
    const edgeRows=model.edges.map(e=>{
      const a=nodesById.get(e.parent),b=nodesById.get(e.child);
      const shared=a?.survives.filter(c=>b?.survives.includes(c))||[];
      return {...e,alive:shared.length>0,emphasis:highlighted?shared.includes(highlighted):shared.length>0};
    }).sort((a,b)=>Number(a.emphasis)-Number(b.emphasis));
    edgeRows.forEach(e=>{
      if(model.edges.length>6000&&!e.alive){omitted++;return;}
      let a=pos.get(e.parent),b=pos.get(e.child);
      if(!b)return;
      if(!a){const parent=nodesById.get(e.parent);if(!parent||parent.generation>=start)return;a={x:left-28,y:b.y};
        const marker=svg("text",{x:left-32,y:b.y-5,class:"rw-boundary"},`←${parent.generation+1}`);marker.append(svg("title",{},`第${parent.generation+1}世代の親から継続`));chart.append(marker);}
      const path=svg("path",{d:`M${a.x},${a.y} C${(a.x+b.x)/2},${a.y} ${(a.x+b.x)/2},${b.y} ${b.x},${b.y}`,class:`rw-edge ${e.alive?"is-alive":""} ${e.emphasis?"is-emphasis":""}`});
      if(e.twice)path.append(svg("title",{},"同じ親が2回選ばれた"));chart.append(path);
    });
    for(const [id,p] of pos){const n=nodesById.get(id);const dot=svg("circle",{cx:p.x,cy:p.y,r:id===selectedNode?6:4,class:`rw-node ${n.survives.length?"is-alive":""} ${!number(n.quality)?"is-unknown":""} ${id===selectedNode?"is-selected":""}`,"fill-opacity":number(n.quality)?Math.max(.25,Math.min(1,.25+.75*n.quality)):0,tabindex:0,role:"button","aria-label":`第${n.generation+1}世代 個体${n.index} ${n.cell||"地図外"} 品質${fmt(n.quality)}`});
      dot.append(svg("title",{},`第${n.generation+1}世代・個体${n.index}・${n.cell||"地図外"}・品質${fmt(n.quality)}`));
      dot.addEventListener("click",()=>selectNode(id));dot.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();selectNode(id);}});chart.append(dot);}
    const bars=model.bars.filter(b=>b.generation>=start&&b.generation<=end);
    const max=Math.max(1,...bars.map(b=>b.count));
    chart.append(svg("text",{x:8,y:y+20},"地図外・子なし（数）"));
    bars.forEach(b=>{const h=32*b.count/max;chart.append(svg("rect",{x:x(b.generation)-15,y:y+48-h,width:30,height:h,class:"rw-river-bar"}));chart.append(svg("text",{x:x(b.generation),y:y+66,"text-anchor":"middle"},b.count));});
    $("[data-river-canvas]").append(chart);
    if(omitted)$("[data-river-canvas]").append(Object.assign(document.createElement("p"),{textContent:`その他の親子の線を${omitted}本省略しています。`}));

    $("[data-node-info]").innerHTML=chosen?`<h3>選択中の個体</h3><p>第${chosen.generation+1}世代・個体${chosen.index}</p><p>${chosen.elite?"この時点で地図に残った代表":"記録された個体"}</p><dl><dt>型</dt><dd>${esc(chosen.cell?.replace("|"," × ")||"地図外")}</dd><dt>品質</dt><dd>${number(chosen.quality)?fmt(chosen.quality):"未計測"}</dd><dt>親</dt><dd>${chosen.parents.map(esc).join("<br>")||"親なし"}</dd></dl>${chosen.elite&&observed.generation===observed.latest?`<a href="/exp/${encodeURIComponent(observed.run_name)}/cell/${encodeURIComponent(chosen.elite)}/lineage">転機を見る ↗</a>`:""}`:'<h3>血筋を選ぶ</h3><p>図の点、または下の表から個体を選ぶと、詳細を確認できます。</p>';
    $("[data-node-table]").innerHTML=`<table><thead><tr><th>世代・個体</th><th>型</th><th>品質</th><th>親</th></tr></thead><tbody>${model.nodes.filter(n=>n.generation>=start&&n.generation<=end).map(n=>`<tr><td><button data-node="${n.id}">第${n.generation+1}世代・${n.index}</button></td><td>${esc(n.cell||"地図外")}</td><td>${number(n.quality)?fmt(n.quality):"未計測"}</td><td>${n.parents.map(esc).join(" / ")||"親なし"}</td></tr>`).join("")}</tbody></table>`;
  }
  function selectNode(id){selectedNode=id;follow=false;generationControls();river();remember(true);$("[data-node-info]").setAttribute("tabindex","-1");$("[data-node-info]").focus({preventScroll:true});}
  const valueText=(v,m=metric)=>m==="reach_rate"?percent(v):fmt(v);
  function trends(){
    const detailOpen=$("[data-trend-detail] details")?.open || false;
    const rows=observed.generations || [];
    $("[data-metric]").value=metric;
    const descriptions={occupied_cells:"各世代の保存時点で、地図に残っている物語の型の数。",reach_rate:"その世代のseed別シミュレーション全件のうち、結末に到達した割合。",average_archive_quality:"その時点で地図に残っている代表群の平均品質。全個体の平均ではありません。"};
    $("[data-metric-description]").textContent=descriptions[metric];
    const host=$("[data-trend-graph]");host.replaceChildren();
    if(!rows.length){host.innerHTML=empty(observed.notice||"最初の世代が保存されると表示されます。");$("[data-trend-table]").replaceChildren();$("[data-trend-detail]").replaceChildren();return;}
    const W=Math.max(480,host.clientWidth || 660),H=Math.max(190,Math.min(420,$(".rw-content").clientHeight-190)),pad=48,max=Math.max(metric==="reach_rate"?1:0,...rows.map(r=>number(r[metric])?r[metric]:0))||1;
    const maxGen=Math.max(...rows.map(r=>r.generation),1),x=g=>pad+g/maxGen*(W-pad*2),y=v=>H-pad-v/max*(H-pad*2);
    const chart=svg("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":descriptions[metric]});
    for(let i=0;i<=4;i++){const value=max*i/4;chart.append(svg("line",{x1:pad,x2:W-pad,y1:y(value),y2:y(value),class:"rw-grid-line"}));chart.append(svg("text",{x:pad-8,y:y(value)+4,"text-anchor":"end"},valueText(value)));}
    let previous=null;
    rows.forEach(row=>{const v=row[metric];if(!number(v)){previous=null;return;}
      if(previous&&row.generation===previous.generation+1)chart.append(svg("line",{x1:x(previous.generation),y1:y(previous[metric]),x2:x(row.generation),y2:y(v),class:"rw-trend-line"}));
      const point=svg("circle",{cx:x(row.generation),cy:y(v),r:row.generation===trendGen?6:4,class:"rw-trend-point",tabindex:0,role:"button","aria-label":`第${row.generation+1}世代 ${valueText(v)}`});
      const choose=()=>{trendGen=row.generation;trends();};point.addEventListener("click",choose);point.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();choose();}});chart.append(point);
      if(rows.length<15||row===rows[0]||row===rows.at(-1)||row.generation===trendGen)chart.append(svg("text",{x:x(row.generation),y:H-15,"text-anchor":"middle"},`第${row.generation+1}世代`));previous=row;});host.append(chart);
    const current=rows.find(r=>r.generation===trendGen)||rows.at(-1);trendGen=current.generation;
    const prev=rows.find(r=>r.generation===current.generation-1);
    const cards=[["物語の種類数","occupied_cells"],["結末到達率","reach_rate"],["地図上の平均品質","average_archive_quality"]].map(([label,m])=>{
      const diff=number(current[m])&&number(prev?.[m])?current[m]-prev[m]:null;
      const diffText=number(diff)?`${diff>=0?"+":""}${fmt(m==="reach_rate"?diff*100:diff)}${m==="reach_rate"?"pt":""}`:"—";
      return `<div><span>${label}</span><strong>${valueText(current[m],m)}</strong><small>前世代比 ${diffText}</small></div>`;
    }).join("");
    const actions=Object.entries(current.action_share||{}).map(([k,v])=>`${esc(payload.action_labels[k]||k)} ${percent(v)}`).join(" / ");
    const changes=prev?.action_share && current.action_share ? [...new Set([...Object.keys(prev.action_share),...Object.keys(current.action_share)])].map(k=>({k,before:prev.action_share[k]||0,after:current.action_share[k]||0})).map(v=>({...v,diff:Math.abs(v.after-v.before)})).filter(v=>v.diff>=.10).sort((a,b)=>b.diff-a.diff||a.k.localeCompare(b.k)).slice(0,3).map(v=>`${esc(payload.action_labels[v.k]||v.k)} ${percent(v.before)}→${percent(v.after)}`).join(" / ") : "";
    $("[data-trend-detail]").innerHTML=`<h3>第${current.generation+1}世代</h3><div class="rw-stats">${cards}</div><p>対峙時の仲間数：${fmt(current.allies_mean_at_contest)}</p><details><summary>行動の変化・内訳</summary><p>大きく変わった行動：${changes||"—"}</p><p>${actions||"記録なし"}</p></details>${observed.historical?`<button data-view-generation="${current.generation}">この世代のリプレイを見る →</button>`:""}`;
    $("[data-trend-detail] details").open=detailOpen;
    $("[data-trend-table]").innerHTML=`<div class="rw-table-wrap"><table><thead><tr><th>世代</th><th>種類数</th><th>結末到達率</th><th>地図上の平均品質</th></tr></thead><tbody>${rows.map(r=>`<tr><td><button data-trend-generation="${r.generation}">第${r.generation+1}世代</button></td><td>${fmt(r.occupied_cells)}</td><td>${percent(r.reach_rate)}</td><td>${fmt(r.average_archive_quality)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  function fitCharts(){
    const area=$(".rw-content");
    root.style.setProperty("--rw-view-height", `${area.clientHeight}px`);
    if(tab==="river") river();
    if(tab==="trends") trends();
  }
  let resizeFrame;
  const areaObserver=new ResizeObserver(()=>{
    cancelAnimationFrame(resizeFrame);
    resizeFrame=requestAnimationFrame(fitCharts);
  });
  areaObserver.observe($(".rw-content"));
  function paintObservation(resetReplay=true){generationControls();overview();river();trends();if(resetReplay)mountReplay();jobState(job);}
  async function loadObservation(generation,manual=false){
    const keepPaused = replay()?.wbIsPaused?.() || false;
    const seq=++requestSequence; const url=new URL(location.href);url.searchParams.set("view-data","1");
    if(job.job_id)url.pathname=`/jobs/${encodeURIComponent(job.job_id)}`;
    if(manual){manualRequest=seq;follow=false;replay()?.wbPause?.();generationControls();}
    if(number(generation))url.searchParams.set("gen",generation);else url.searchParams.delete("gen");
    try{
      const response=await fetch(url,{cache:"no-store"});if(!response.ok)throw new Error();
      const next=await response.json();if(seq!==requestSequence)return;
      if(next.observation.notice && observed.replay && !next.observation.replay && next.observation.historical===false && next.job.job_id)throw new Error();
      const same=observed.generation===next.observation.generation;
      observed=next.observation;payload=next;
      if(manual){follow=false;selectedNode="";}
      if(follow){selectedNode="";rangeEnd=observed.generation;}
      paintObservation(!same||!replay());
      if(keepPaused)replay()?.wbPause?.();
      jobState(next.job);remember(manual);
      const newer = !follow && observed.latest > observed.generation;
      $("[data-new-generation]").hidden=!newer;
      if(newer)$("[data-new-generation]").textContent=`第${observed.latest+1}世代まで保存済みです。表示は第${observed.generation+1}世代に固定しています。`;
    }catch(_){if(seq!==requestSequence)return;generationControls();remember();$("[data-connection]").hidden=false;$("[data-connection]").textContent="記録を読み込めませんでした。表示中の記録を保持しています。";}
    finally{if(manualRequest===seq){manualRequest=0;generationControls();}}
  }
  root.addEventListener("change",e=>{
    if(e.target.matches("[data-generation]"))loadObservation(Number(e.target.value),true);
    if(e.target.matches("[data-speed]")){playbackSpeed=Number(e.target.value);replay()?.wbSpeed?.(playbackSpeed);}
    if(e.target.matches("[data-metric]")){metric=e.target.value;trends();remember();}
    if(e.target.matches("[data-range]")){rangeMode=e.target.value;river();}
  });
  root.addEventListener("click",e=>{
    if(e.target.closest("[data-prev-generation]") && observed.generation>0)loadObservation(observed.generation-1,true);
    if(e.target.closest("[data-next-generation]") && observed.generation<observed.latest)loadObservation(observed.generation+1,true);
    if(e.target.closest("[data-go-live]")){
      follow=true;selectedNode="";generationControls();remember(true);loadObservation(null);
    }
    const node=e.target.closest("[data-node]");if(node)selectNode(node.dataset.node);
    const tg=e.target.closest("[data-trend-generation]");if(tg){trendGen=Number(tg.dataset.trendGeneration);trends();}
    const vg=e.target.closest("[data-view-generation]");if(vg){switchTab("replay");loadObservation(Number(vg.dataset.viewGeneration),true);}
  });
  $("[data-range-prev]").addEventListener("click",()=>{rangeEnd=Math.max(0,rangeEnd-(innerWidth<768?3:7));river();});
  $("[data-range-next]").addEventListener("click",()=>{rangeEnd=Math.min(observed.generation,rangeEnd+(innerWidth<768?3:7));river();});
  $("[data-clear-node]").addEventListener("click",()=>{selectedNode="";river();remember(true);});
  root.addEventListener("rw-replay-generation",e=>loadObservation(e.detail,true));
  root.addEventListener("rw-replay-ended",()=>{
    if(follow && tab==="replay" && !document.hidden && !replay()?.wbIsPaused?.()){
      const next=(observed.generation??-1)+1;
      if(next<pendingRevision)loadObservation(next);
    }
  });
  async function poll(){
    if(!job.job_id || terminal.has(job.state))return;
    try{
      const response=await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}`,{cache:"no-store"});
      if(!response.ok)throw new Error();const next=await response.json();
      jobState(next);lastConfirmed=new Date().toLocaleTimeString("ja-JP");$("[data-last-update]").textContent=`最終確認 ${lastConfirmed}`;
      $("[data-connection]").hidden=false;$("[data-connection]").textContent=next.reconciliation==="unknown"?"実行プロセスの状態を確認しています。":"";$("[data-connection]").hidden=next.reconciliation!=="unknown";
      pendingRevision=Number(next.publication_revision||0);
      if(!manualRequest && pendingRevision!==Number(observed.revision||0)){
        const r=replay();
        if(follow&&tab==="replay"&&r&&(!r.dataset.finished||r.wbIsPaused?.())){
          $("[data-new-generation]").hidden=false;$("[data-new-generation]").textContent=`第${pendingRevision}世代の記録が届きました。再生中の記録を保持しています。`;
        }else await loadObservation(follow?null:observed.generation);
      }
      if(!manualRequest && terminal.has(next.state))await loadObservation(follow&&tab!=="replay"?null:observed.generation);
    }catch(_){$("[data-connection]").hidden=false;$("[data-connection]").textContent=`接続を確認中。${lastConfirmed?`最終確認 ${lastConfirmed}。`:""}最後に確認した状態を表示しています。`;}
    if(!terminal.has(job.state))timer=setTimeout(poll,2000);
  }
  $("[data-stop]").addEventListener("click",async()=>{
    if(stoppingRequested)return;stoppingRequested=true;jobState(job);
    $("[data-stop-status]").textContent="停止を要求しています…";
    try{
      const response=await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/cancel`,{method:"POST",headers:{"Content-Type":"application/json","X-WorldBloom-Client":"1"},body:"{}"});
      if(!response.ok){const error=await response.json();stoppingRequested=false;jobState(job);$("[data-stop-status]").textContent=error.message||"停止を要求できませんでした。";return;}
      $("[data-stop-status]").textContent="停止を要求しました。保存済みの結果は残ります。";
    }catch(_){$("[data-stop-status]").textContent="停止要求の成否を確認中です。実行状態を再確認しています。";}
  });
  window.addEventListener("popstate",()=>{
    const url=new URL(location.href);follow=!url.searchParams.has("gen");selectedNode=url.searchParams.get("node")||"";
    const selectedMetric=url.searchParams.get("metric");
    if(["occupied_cells","reach_rate","average_archive_quality"].includes(selectedMetric))metric=selectedMetric;
    const next=url.searchParams.get("tab")||"overview";switchTab(tabNames.includes(next)?next:"overview",false);
    loadObservation(follow?null:Number(url.searchParams.get("gen")));
  });
  window.addEventListener("pagehide",()=>{clearTimeout(timer);window.WorldBloomReplay?.stop();});
  window.addEventListener("pageshow",event=>{if(event.persisted){mountReplay();replay()?.wbPause?.();if(job.job_id&&!terminal.has(job.state))timer=setTimeout(poll,100);}});
  $("[data-metric]").value=metric;
  switchTab(tab,false);paintObservation();
  if(job.job_id&&!terminal.has(job.state))timer=setTimeout(poll,100);
})();
