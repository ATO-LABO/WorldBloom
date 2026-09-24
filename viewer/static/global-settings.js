(() => {
  "use strict";
  const root = document.querySelector("[data-global-settings]");
  if (!root) return;
  const initial = JSON.parse(root.dataset.initial);
  const $ = (selector, scope = root) => scope.querySelector(selector);
  const $$ = (selector, scope = root) => [...scope.querySelectorAll(selector)];
  const form = $("[data-gs-output]");
  let loadModels = () => {};
  const TABS = ["output", "compute", "configs", "genres"];
  const urlState = () => {
    const url = new URL(location.href);
    const legacy = url.hash.slice(1);
    return { tab: TABS.includes(legacy) ? legacy : url.searchParams.get("tab") || initial.tab, item: url.searchParams.get("item") };
  };
  function show(tab, item) {
    if (!TABS.includes(tab)) tab = "output";
    $$("[data-gs-panel]").forEach(p => { p.hidden = p.dataset.gsPanel !== tab; });
    $$("[data-gs-tab]").forEach(a => {
      if (a.dataset.gsTab === tab) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    const panel = $(`[data-gs-panel="${tab}"]`);
    const rows = $$("[data-gs-item]", panel);
    if (rows.length) {
      const chosen = rows.find(r => r.dataset.gsItem === item) || rows.find(r => r.hasAttribute("aria-current")) || rows[0];
      rows.forEach(r => r === chosen ? r.setAttribute("aria-current", "true") : r.removeAttribute("aria-current"));
      $$("[data-gs-detail]", panel).forEach(d => { d.hidden = d.dataset.gsDetail !== chosen.dataset.gsItem; });
    }
    if (tab === "output") loadModels();
  }
  root.addEventListener("click", event => {
    const a = event.target.closest("[data-gs-tab], [data-gs-item]");
    if (!a || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    history.pushState(null, "", a.href);
    const state = urlState(); show(state.tab, state.item);
  });
  const syncURL = () => { const state = urlState(); show(state.tab, state.item); };
  addEventListener("popstate", syncURL);
  addEventListener("hashchange", syncURL);
  $$("[data-gs-search]").forEach(input => input.addEventListener("input", () => {
    const panel = input.closest("[data-gs-panel]");
    const rows = $$("[data-gs-item]", panel);
    const query = input.value.trim().toLocaleLowerCase();
    rows.forEach(r => { r.hidden = !r.dataset.search.toLocaleLowerCase().includes(query); });
    const count = rows.filter(r => !r.hidden).length;
    $("[data-gs-count]", panel).textContent = `${count} / ${rows.length}件`;
    $("[data-gs-no-match]", panel).hidden = count !== 0 || rows.length === 0;
  }));
  (function setupCompute() {
    const cform = $("[data-gs-compute]");
    if (!cform) return;
    let csaved = initial.compute;
    let cbusy = false;
    const cfield = $("#gs-processes", cform);
    const cvalues = () => ({ processes: cfield.value === "" ? null : Number(cfield.value) });
    const cdifferent = () => JSON.stringify(cvalues()) !== JSON.stringify({ processes: csaved.processes });
    function cdrawSaved() {
      const dl = $("[data-gs-compute-saved]", cform); dl.replaceChildren();
      appendText(dl, "dt", "並列数"); appendText(dl, "dd", `${csaved.processes}`);
    }
    function cupdate(message) {
      const changed = cdifferent();
      const badge = $("[data-gs-compute-dirty]");
      badge.textContent = changed ? "未保存の変更" : "保存済み";
      badge.classList.toggle("is-dirty", changed);
      $("[data-gs-compute-save]", cform).disabled = cbusy || !changed;
      $("[data-gs-compute-reset]", cform).disabled = cbusy || !changed;
      $("[data-gs-compute-fields]", cform).disabled = cbusy;
      $("[data-gs-compute-status]", cform).textContent =
        message || (changed ? "変更はまだ保存されていません" : "保存済みの設定を表示しています");
    }
    cform.addEventListener("input", () => { $("[data-compute-form-error]", cform).textContent = ""; cupdate(); });
    $("[data-gs-compute-reset]", cform).addEventListener("click", () => {
      cfield.value = csaved.processes; cupdate("変更を戻しました。保存済みの設定を表示しています");
    });
    cform.addEventListener("submit", async event => {
      event.preventDefault();
      if (cbusy || !cdifferent() || !cform.reportValidity()) return;
      cbusy = true; cupdate("設定を保存中…");
      try {
        csaved = await api("/api/settings/evolution", cvalues());
        cfield.value = csaved.processes; cdrawSaved();
        cbusy = false; cupdate("設定を保存しました。次に開始するGA実験から適用されます");
      } catch (e) {
        cbusy = false;
        $("[data-compute-form-error]", cform).textContent = (e && e.message) || "保存できませんでした";
        cupdate("保存できませんでした。変更内容は残っています");
      }
    });
    addEventListener("beforeunload", event => {
      if (cdifferent() || cbusy) { event.preventDefault(); event.returnValue = ""; }
    });
    cdrawSaved(); cupdate();
  })();
  if (!form) { syncURL(); return; }

  let saved = initial.view;
  let backends = saved.backends;
  let busy = false;
  let modelSequence = 0;
  let catalogBackend = null;
  const fields = $$("[data-field]", form);
  const field = name => fields.find(el => el.dataset.field === name);
  const backend = () => field("backend").value;
  const keys = ["max_calls", "call_timeout_seconds", "wall_seconds", "max_saved_response_bytes"];
  const labels = ["候補数の上限", "1件の待ち時間", "全体の待ち時間", "応答の保存上限"];
  const units = ["件", "秒", "秒", "bytes"];
  const keyInput = $("[data-gs-key-input]");
  const drafts = new Map();
  let priorBackend = saved.backend;
  function values() {
    return { backend: backend(), model: backend() === "none" ? null : field("model").value.trim() || null,
      limits: Object.fromEntries(keys.map(k => [k, field(`limits.${k}`).value === "" ? null : Number(field(`limits.${k}`).value)])) };
  }
  const different = () => JSON.stringify(values()) !== JSON.stringify({backend:saved.backend, model:saved.model, limits:Object.fromEntries(keys.map(k => [k,saved.limits[k]]))});
  const hiddenKeys = () => [...drafts].some(([name, draft]) => name !== backend() && draft.key);
  const dirty = () => different() || keyInput.value.length > 0 || hiddenKeys();
  function appendText(parent, tag, value) {
    const el = document.createElement(tag); el.textContent = value; parent.append(el); return el;
  }
  function drawSaved() {
    const dl = $("[data-gs-saved]"); dl.replaceChildren();
    const pairs = [["生成方式", initial.labels[saved.backend]], ["モデル", saved.model || "指定なし"], ...keys.map((k, i) => [labels[i], `${saved.limits[k].toLocaleString()} ${units[i]}`])];
    pairs.forEach(([label, value]) => { appendText(dl, "dt", label); appendText(dl, "dd", value); });
  }
  function update(message) {
    const changed = different();
    const badge = $("[data-gs-dirty]");
    badge.textContent = dirty() ? "未保存の変更" : "保存済み";
    badge.classList.toggle("is-dirty", dirty());
    $("[data-gs-save]").disabled = busy || !changed;
    $("[data-gs-reset]").disabled = busy || !dirty();
    $("[data-gs-fields]").disabled = busy;
    field("model").disabled = backend() === "none";
    $("[data-gs-test]").disabled = busy || backend() === "none";
    $("[data-gs-key-save]").disabled = busy || !keyInput.value.trim();
    $("[data-gs-save-status]").textContent = message || (changed ? "変更はまだ保存されていません" : keyInput.value ? "APIキーは「キーを保存」で保存してください" : hiddenKeys() ? "別の生成方式に未保存のAPIキーがあります" : "保存済みの設定を表示しています");
    const diff = $("[data-gs-diff]"); diff.replaceChildren();
    if (changed) {
      appendText(diff, "strong", "保存すると変わる項目");
      const draft = values();
      if (draft.backend !== saved.backend) appendText(diff,"p",`生成方式：${initial.labels[saved.backend]} → ${initial.labels[draft.backend]}`);
      if (draft.model !== saved.model) appendText(diff,"p",`モデル：${saved.model || "指定なし"} → ${draft.model || "指定なし"}`);
      keys.forEach((k,i) => { if (draft.limits[k] !== saved.limits[k]) appendText(diff,"p",`${labels[i]}：${saved.limits[k]} → ${draft.limits[k] ?? "未入力"} ${units[i]}`); });
    }
  }
  function backendUI() {
    $("[data-gs-key]").hidden = !["anthropic", "openai"].includes(backend());
    $("[data-gs-key-status]").textContent = backends[backend()].has_api_key ? "キー登録済み" : "キー未登録";
    field("limits.max_calls").min = backend() === "none" ? "0" : "1";
    $("[data-gs-connection]").textContent = backend() === "none" ? "生成を行わずプロンプトを保存します" : "未確認";
  }
  function fill(view) {
    field("backend").value = view.backend;
    field("model").value = view.model || "";
    keys.forEach(k => { field(`limits.${k}`).value = view.limits[k]; });
    priorBackend = view.backend;
    backendUI(); update();
  }
  function errors(payload = {}) {
    $("[data-form-error]").textContent = payload.message || "";
    const map = payload.field_errors || {};
    $$("[data-error-for]").forEach(el => { el.textContent = map[el.dataset.errorFor] || ""; });
  }
  async function api(path, payload) {
    const response = await fetch(path, payload === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-WorldBloom-Client":"1"}, body:JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw result;
    return result;
  }
  loadModels = async (force = false) => {
    const selected = backend();
    if (!force && catalogBackend === selected) return;
    catalogBackend = selected;
    const sequence = ++modelSequence;
    const list = $("#output-model-list"); list.replaceChildren();
    const hint = $("[data-gs-model-hint]");
    if (selected === "none") { hint.textContent = "モデルの指定は不要です。"; return; }
    hint.textContent = "モデルの候補を確認中…";
    try {
      const result = await api(`/api/settings/output/models?backend=${encodeURIComponent(selected)}`);
      if (sequence !== modelSequence || backend() !== selected) return;
      (result.models || []).forEach(model => { const option = document.createElement("option"); option.value = model; list.append(option); });
      hint.textContent = result.reason_label || (result.live ? "候補から選ぶか、モデル名を入力してください。" : "モデル名を入力してください。接続確認済みのモデルは候補に表示されます。");
    } catch (e) {
      if (sequence === modelSequence && backend() === selected) hint.textContent = "モデルの候補を取得できませんでした。モデル名を直接入力できます。";
    }
  };
  form.addEventListener("input", event => {
    if (event.target === field("model")) $("[data-gs-connection]").textContent = "未確認";
    errors(); update();
  });
  field("backend").addEventListener("change", () => {
    // Retain per-backend edits in memory, never in browser storage.
    const old = {backend:priorBackend, model:field("model").value.trim() || null, limits:values().limits, key:keyInput.value};
    drafts.set(priorBackend, old);
    const next = drafts.get(backend()) || {backend:backend(), ...backends[backend()]};
    keyInput.value = next.key || "";
    fill(next); errors(); loadModels();
  });
  $("[data-gs-reset]").addEventListener("click", () => {
    drafts.clear(); keyInput.value = ""; fill(saved); errors(); loadModels(true);
    update("変更を戻しました。保存済みの設定を表示しています");
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !different() || !form.reportValidity()) return;
    const payload = values(); busy = true; errors(); update("設定を保存中…");
    try {
      saved = await api("/api/settings/output", payload);
      backends = saved.backends; drafts.clear(); fill(saved); drawSaved();
      busy = false; update(keyInput.value ? "設定を保存しました。APIキーはまだ保存されていません" : "設定を保存しました。次の生成から適用されます");
    } catch (e) { busy = false; errors(e); update("保存できませんでした。変更内容は残っています"); }
  });
  $("[data-gs-test]").addEventListener("click", async () => {
    if (busy) return;
    busy = true; errors(); update(); $("[data-gs-connection]").textContent = "確認中…";
    try {
      const result = await api("/api/settings/output/test", {backend:backend(), model:values().model});
      backends = result.backends;
      $("[data-gs-connection]").textContent = result.availability.label;
      loadModels(true);
    } catch (e) { $("[data-gs-connection]").textContent = "確認できませんでした"; errors(e); }
    busy = false; update();
  });
  $("[data-gs-key-save]").addEventListener("click", async () => {
    if (busy || !keyInput.value.trim()) return;
    busy = true; errors(); update(); $("[data-gs-key-status]").textContent = "保存中…";
    try {
      const result = await api("/api/settings/output/api-key", {backend:backend(), api_key:keyInput.value.trim()});
      backends = result.backends; keyInput.value = "";
      const draft = drafts.get(backend()); if (draft) delete draft.key;
      $("[data-gs-key-status]").textContent = "キーを保存しました";
      $("[data-gs-connection]").textContent = "未確認"; loadModels(true);
    } catch (e) { $("[data-gs-key-status]").textContent = "保存できませんでした"; errors(e); }
    busy = false; update();
  });
  addEventListener("beforeunload", event => {
    if (dirty() || busy) { event.preventDefault(); event.returnValue = ""; }
  });
  fill(saved); drawSaved(); syncURL();
})();
