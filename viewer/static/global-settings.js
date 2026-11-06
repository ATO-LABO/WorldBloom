(() => {
  "use strict";
  const root = document.querySelector("[data-global-settings]");
  if (!root) return;
  const initial = JSON.parse(root.dataset.initial);
  const $ = (selector, scope = root) => scope.querySelector(selector);
  const $$ = (selector, scope = root) => [...scope.querySelectorAll(selector)];
  const form = $("[data-gs-output]");
  const TABS = ["output", "compute", "configs", "genres"];
  function mountTabs(container, onActivate) {
    const buttons = $$(":scope > .ui-tabs > button", container);
    const panels = $$('[role="tabpanel"]', container);
    const scroll = $(".gs-form-scroll", container);
    function activate(panelId) {
      const selected = buttons.find(button => button.getAttribute("aria-controls") === panelId);
      if (!selected || selected.disabled || selected.getAttribute("aria-selected") === "true") return;
      buttons.forEach(button => {
        const active = button === selected;
        button.setAttribute("aria-selected", String(active));
        button.tabIndex = active ? 0 : -1;
      });
      panels.forEach(panel => { panel.hidden = panel.id !== panelId; });
      if (scroll) scroll.scrollTop = 0;
    }
    buttons.forEach(button => button.addEventListener("click", () => {
      const panelId = button.getAttribute("aria-controls");
      if (onActivate) onActivate(panelId);
      else activate(panelId);
    }));
    return activate;
  }
  function revealField(control, activate) {
    const panel = control.closest('[role="tabpanel"]');
    if (panel?.hidden) activate(panel.id);
  }

  const configSection = $('[data-gs-panel="configs"]');
  const activateConfigs = mountTabs(configSection, panelId => {
    const url = new URL(location.href);
    if (panelId === "gs-configs-panel-detail") {
      const current = $("[data-gs-item][aria-current]", configSection);
      if (!current) return;
      url.searchParams.set("item", current.dataset.gsItem);
    } else {
      url.searchParams.delete("item");
    }
    if (url.href !== location.href) history.pushState(null, "", url.href);
    syncURL();
  });
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
      if (tab === "configs") activateConfigs(item ? "gs-configs-panel-detail" : "gs-configs-panel-list");
    }
  }
  root.addEventListener("click", event => {
    const a = event.target.closest("[data-gs-tab], [data-gs-item]");
    if (!a || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    history.pushState(null, "", a.href);
    syncURL();
    if (configSection.contains(a) && a.hasAttribute("data-gs-item")) $("[data-gs-detail]:not([hidden]) h2", configSection)?.focus();
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
    const tfield = $("#gs-thermal-enabled", cform);
    const pfield = $("#gs-pause_at", cform);
    const footer = $("[data-gs-compute-save]", cform).closest("footer");
    const activateTab = mountTabs(cform, showComputeTab);
    function showComputeTab(panelId) {
      activateTab(panelId);
      footer.hidden = panelId === "gs-compute-panel-jev";
    }
    const num = f => f.value === "" ? null : Number(f.value);
    const cvalues = () => ({ processes: num(cfield), thermal_enabled: tfield.checked, pause_at: num(pfield) });
    const pick = s => ({ processes: s.processes, thermal_enabled: s.thermal_enabled, pause_at: s.pause_at });
    const cdifferent = () => JSON.stringify(cvalues()) !== JSON.stringify(pick(csaved));
    const cfill = () => { cfield.value = csaved.processes; tfield.checked = csaved.thermal_enabled; pfield.value = csaved.pause_at; };
    function cdrawSaved() {
      $("[data-gs-compute-saved-processes]", cform).textContent = csaved.processes;
      $("[data-gs-compute-saved-thermal]", cform).textContent =
        csaved.thermal_enabled ? `オン（${csaved.pause_at}℃で一時停止）` : "オフ";
    }
    function cupdate(message) {
      const changed = cdifferent();
      const badge = $("[data-gs-compute-dirty]");
      badge.textContent = changed ? "未保存の変更" : "保存済み";
      badge.classList.toggle("is-dirty", changed);
      $("[data-gs-compute-save]", cform).disabled = cbusy || !changed;
      $("[data-gs-compute-reset]", cform).disabled = cbusy || !changed;
      $("[data-gs-compute-fields]", cform).disabled = cbusy;
      pfield.disabled = cbusy || !tfield.checked;
      $("[data-gs-compute-status]", cform).textContent =
        message || (changed ? "変更はまだ保存されていません" : "保存済みの設定を表示しています");
    }
    $("[data-gs-compute-fields]", cform).addEventListener("input", () => {
      $("[data-compute-form-error]", cform).textContent = "";
      cupdate();
    });
    $("[data-gs-compute-reset]", cform).addEventListener("click", () => {
      cfill(); cupdate("変更を戻しました。保存済みの設定を表示しています");
    });
    cform.addEventListener("submit", async event => {
      event.preventDefault();
      if (cbusy || footer.hidden || !cdifferent()) return;
      const invalid = [cfield, pfield].find(control => control.willValidate && !control.validity.valid);
      if (invalid) {
        revealField(invalid, showComputeTab);
        invalid.reportValidity();
        return;
      }
      cbusy = true; cupdate("設定を保存中…");
      try {
        csaved = await api("/api/settings/evolution", cvalues());
        cfill(); cdrawSaved();
        cbusy = false; cupdate("設定を保存しました。次に開始するGA実験から適用されます");
      } catch (e) {
        cbusy = false;
        $("[data-compute-form-error]", cform).textContent = (e && e.message) || "保存できませんでした";
        cupdate("保存できませんでした。変更内容は残っています");
        const invalid = [cfield, tfield, pfield].find(control => e?.field_errors?.[control.dataset.field]);
        if (invalid) {
          revealField(invalid, showComputeTab);
          (invalid.disabled ? tfield : invalid).focus();
        }
      }
    });
    addEventListener("beforeunload", event => {
      if (cdifferent() || cbusy) { event.preventDefault(); event.returnValue = ""; }
    });
    cdrawSaved(); cupdate();
  })();
  (function setupJev() {
    const saveButton = $("[data-gs-jev-key-save]");
    const input = $("[data-gs-jev-key-input]");
    const status = $("[data-gs-jev-key-status]");
    if (!saveButton || !input || !status) return;
    let busy = false;
    input.addEventListener("input", () => { saveButton.disabled = !input.value.trim(); });
    input.addEventListener("keydown", event => {
      if (event.key !== "Enter" || event.isComposing) return;
      event.preventDefault();
      saveButton.click();
    });
    saveButton.disabled = true;
    saveButton.addEventListener("click", async () => {
      if (busy || !input.value.trim()) return;
      busy = true; saveButton.disabled = true; status.textContent = "保存中…";
      try {
        const response = await fetch("/api/settings/rationality/jev-key", {
          method: "POST", headers: {"Content-Type": "application/json", "X-WorldBloom-Client": "1"},
          body: JSON.stringify({api_key: input.value.trim()}),
        });
        const result = await response.json();
        if (!response.ok) throw result;
        input.value = "";
        status.textContent = `有効（${result.model}、${new Date(result.verified_at).toLocaleString()} 確認）`;
      } catch (e) {
        status.textContent = (e && e.message) || "保存できませんでした";
      }
      busy = false; saveButton.disabled = !input.value.trim();
    });
  })();
  if (!form) { syncURL(); return; }

  const activateOutput = mountTabs(form);

  let saved = initial.view;
  let busy = false;
  let gemmaSelected = false;
  const chooseGemma = $("[data-gs-gemma-profile]", form);
  const profileNote = $("[data-gs-profile-note]", form);
  const gemmaHelp = profileNote.textContent;
  const fields = $$("[data-field]", form);
  const field = name => fields.find(el => el.dataset.field === "limits." + name);
  const keys = ["max_calls", "call_timeout_seconds", "wall_seconds", "max_saved_response_bytes"];
  const labels = ["AI呼出しの上限", "1件の待ち時間", "全体の待ち時間", "応答の保存上限"];
  const units = ["件", "秒", "秒", "bytes"];
  const hiddenModel = value => /qwen[\s_-]*3[.：:_-]?8/i.test(value || "");
  const modelLabel = value => hiddenModel(value) ? "既存モデル（内部設定を保持）" : value || "指定なし";
  const gemmaModel = "gemma4:e4b-it-qat";
  const gemmaProfile = "gemma4-e4b-qat";
  const backend = () => gemmaSelected ? "ollama" : saved.backend;
  const gemmaConfigured = () => saved.backend === "ollama" && saved.model === gemmaModel && saved.profile === gemmaProfile;
  const limitValue = key => field(key).value === "" ? null : Number(field(key).value);
  const draftLimits = () => Object.fromEntries(keys.map(key => [key, limitValue(key)]));
  const different = () => (gemmaSelected && !gemmaConfigured())
    || keys.some(key => limitValue(key) !== saved.limits[key]);
  function appendText(parent, tag, value) {
    const el = document.createElement(tag); el.textContent = value; parent.append(el); return el;
  }
  function drawSaved() {
    const dl = $("[data-gs-saved]");
    const limits = $("[data-gs-saved-limits]");
    dl.replaceChildren();
    limits.replaceChildren();
    const pairs = [["生成方式", initial.labels[saved.backend]], ["モデル", modelLabel(saved.model)]];
    if (saved.profile === gemmaProfile) pairs.push(["専用プロファイル", gemmaProfile]);
    pairs.forEach(([label, value]) => { appendText(dl, "dt", label); appendText(dl, "dd", value); });
    keys.forEach((k, i) => {
      appendText(limits, "dt", labels[i]);
      appendText(limits, "dd", `${saved.limits[k].toLocaleString()} ${units[i]}`);
    });
  }
  function update(message) {
    const changed = different();
    $("[data-gs-save]").disabled = busy || !changed;
    $("[data-gs-reset]").disabled = busy || !changed;
    $("[data-gs-fields]").disabled = busy;
    $("[data-gs-limits-backend]").textContent = initial.labels[backend()];
    field("max_calls").min = backend() === "none" ? "0" : "1";
    profileNote.textContent = gemmaSelected && !gemmaConfigured()
      ? "Gemma 4を選択しました。「設定を保存」で反映します。"
      : gemmaConfigured() ? "Gemma 4の専用設定は保存済みです。" : gemmaHelp;
    $("[data-gs-save-status]").textContent = message || (changed ? "未保存の変更があります" : "保存済みの設定を表示しています");
    const diff = $("[data-gs-diff]"); diff.replaceChildren();
    if (changed) {
      appendText(diff, "strong", "保存すると変わる項目");
      const limits = draftLimits();
      if (backend() !== saved.backend) appendText(diff, "p", `生成方式：${initial.labels[saved.backend]} → ${initial.labels[backend()]}`);
      if (gemmaSelected && !gemmaConfigured()) appendText(diff, "p", `モデルと専用プロファイル：${gemmaModel} / ${gemmaProfile}`);
      keys.forEach((key, i) => {
        if (limits[key] !== saved.limits[key]) {
          appendText(diff, "p", `${labels[i]}：${saved.limits[key]} → ${limits[key] ?? "未入力"} ${units[i]}`);
        }
      });
    }
  }
  function restoreSaved() {
    gemmaSelected = false;
    keys.forEach(key => { field(key).value = saved.limits[key]; });
  }
  function errors(payload = {}) {
    $("[data-form-error]").textContent = payload.message || "";
    const map = payload.field_errors || {};
    $$("[data-error-for]", form).forEach(el => { el.textContent = map[el.dataset.errorFor] || ""; });
  }
  async function api(path, payload) {
    const response = await fetch(path, payload === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-WorldBloom-Client":"1"}, body:JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw result;
    return result;
  }
  form.addEventListener("input", () => { errors(); update(); });
  chooseGemma.addEventListener("click", () => {
    if (busy) return;
    if (backend() !== "ollama") {
      // Use Ollama defaults for untouched fields and keep any unsaved edits.
      keys.forEach(key => {
        if (limitValue(key) === saved.limits[key]) field(key).value = saved.backends.ollama.limits[key];
      });
    }
    gemmaSelected = true;
    const cap = field("max_calls");
    if (cap.value !== "" && Number(cap.value) < 3) cap.value = 3;
    errors(); update();
  });
  $("[data-gs-reset]").addEventListener("click", () => {
    restoreSaved(); errors();
    update("変更を戻しました。保存済みの設定を表示しています");
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !different()) return;
    const invalid = fields.find(control => !control.validity.valid);
    if (invalid) {
      revealField(invalid, activateOutput);
      invalid.reportValidity();
      return;
    }
    const payload = {backend: backend(), limits: draftLimits()};
    if (gemmaSelected && !gemmaConfigured()) Object.assign(payload, {model: gemmaModel, profile: gemmaProfile});
    busy = true; errors(); update("設定を保存中…");
    try {
      saved = await api("/api/settings/output", payload);
      restoreSaved(); drawSaved();
      root.dispatchEvent(new CustomEvent("worldbloom:output-saved"));
      busy = false;
      update("設定を保存しました。次の生成から適用されます");
    } catch (e) {
      busy = false; errors(e); update("保存できませんでした。変更内容は残っています");
      const invalidField = fields.find(control => e?.field_errors?.[control.dataset.field]);
      if (invalidField) { revealField(invalidField, activateOutput); invalidField.focus(); }
    }
  });
  addEventListener("beforeunload", event => {
    if (different() || busy) { event.preventDefault(); event.returnValue = ""; }
  });
  restoreSaved(); drawSaved(); update(); syncURL();
})();
