"use strict";
(() => {
  const host = document.querySelector("[data-facts]");
  if (!host) return;
  const cfg = JSON.parse(host.dataset.facts);
  const message = host.querySelector("[data-fact-message]");
  if (!message) return;
  const base = "/api/facts/runs/" + encodeURIComponent(cfg.run_id) + "/" + encodeURIComponent(cfg.candidate_id);
  let dirty = false, busy = false;
  const generation = [...host.querySelectorAll('[data-fact-action="generate"]')];
  const requestKey = kind => "wb-facts-request:" + JSON.stringify([cfg.run_id, cfg.candidate_id,
    cfg.revision, cfg.package_sha256, kind]);
  function retryLabel(button) {
    button.dataset.originalLabel ??= button.textContent;
    try {
      button.textContent = sessionStorage.getItem(requestKey(button.dataset.kind))
        ? "生成の受付を確認" : button.dataset.originalLabel;
    } catch (_) { /* Generation itself requires durable request storage below. */ }
  }
  generation.forEach(retryLabel);
  function changed() {
    dirty = true;
    generation.forEach(b => b.disabled = true);
    host.querySelector("[data-fact-state]").textContent = "保存前の下書き · 生成には未適用";
    message.textContent = "変更をプレビューして、新しい版として保存してください。";
  }
  host.addEventListener("input", e => {
    if (e.target.matches("[data-fact-style],[data-fact-viewpoint],[data-fact-synopsis]")) changed();
  });
  host.addEventListener("change", e => {
    if (!e.target.matches("[data-fact-flag]")) return;
    const peers = [...host.querySelectorAll("[data-record]")].filter(x => x.dataset.record === e.target.dataset.record);
    const flag = e.target.dataset.factFlag;
    if (flag === "hidden" && e.target.checked) peers.filter(x => x !== e.target).forEach(x => x.checked = false);
    if ((flag === "required" || flag === "selected") && e.target.checked) peers.find(x => x.dataset.factFlag === "hidden").checked = false;
    if (flag === "required" && e.target.checked) peers.find(x => x.dataset.factFlag === "selected").checked = true;
    if (flag === "selected" && !e.target.checked) peers.find(x => x.dataset.factFlag === "required").checked = false;
    changed();
  });
  function draft() {
    const selection = {selected: [], required: [], hidden: [], viewpoint: host.querySelector("[data-fact-viewpoint]").value};
    host.querySelectorAll("[data-fact-flag]:checked").forEach(x => selection[x.dataset.factFlag].push(x.dataset.record));
    return {config_id: cfg.config_id, selection,
      styles: Object.fromEntries([...host.querySelectorAll("[data-fact-style]")].map(x => [x.dataset.factStyle, x.value])),
      synopsis_output_id: host.querySelector("[data-fact-synopsis]").value.trim() || null};
  }
  async function post(action, body) {
    const response = await fetch(base + "/" + action, {method: "POST", headers: {"Content-Type": "application/json", "X-WorldBloom-Client": "1"}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || "保存できませんでした");
    return data;
  }
  host.addEventListener("click", async e => {
    const button = e.target.closest("[data-fact-action]");
    if (!button || busy) return;
    const action = button.dataset.factAction;
    busy = true; button.disabled = true; message.textContent = "確認しています…";
    try {
      if (action === "reset") {
        host.querySelectorAll("[data-fact-style]").forEach(x => x.value = cfg.defaults[x.dataset.factStyle]);
        changed(); message.textContent = "書き方を既定値に戻した下書きです。保存済み版と生成結果は保持しています。";
      } else if (action === "preview") {
        const data = cfg.readonly ? cfg.saved_preview : await post("preview", draft());
        const kind = button.dataset.kind;
        host.querySelector("[data-fact-preview]").hidden = false;
        host.querySelector("[data-fact-preview-title]").textContent = (kind === "synopsize" ? "あらすじ" : "本文") + "に使う入力とプロンプト";
        host.querySelector("[data-fact-input]").textContent = JSON.stringify(data.inputs[kind], null, 2);
        host.querySelector("[data-fact-prompt]").textContent = data.prompts[kind] || "矛盾する指示があるため、生成用プロンプトを確定できません。";
        const conflicts = data.conflicts[kind] || [];
        host.querySelector("[data-fact-conflicts]").textContent = conflicts.length ? "事実保持条件と矛盾する指示があります：" + conflicts.map(x => x.phrase).join("、") : "";
        message.textContent = "プレビューしました。生成は開始していません。";
      } else if (action === "save") {
        await post("save", {...draft(), expected_revision: cfg.revision});
        location.reload();
      } else if (action === "generate") {
        if (dirty || cfg.readonly) throw new Error("編集内容を保存してから生成してください");
        const key = requestKey(button.dataset.kind);
        const requestId = sessionStorage.getItem(key) || "req-" + crypto.randomUUID();
        // Persist before dispatch: a lost response/reload must replay admission,
        // even when the original job has already finished.
        sessionStorage.setItem(key, requestId);
        const data = await post("generate", {expected_revision: cfg.revision, package_sha256: cfg.package_sha256,
          kind: button.dataset.kind, request_id: requestId});
        if (!data.job || typeof data.job.job_id !== "string" || data.url !== "/jobs/" + data.job.job_id)
          throw new Error("生成の受付を確認できませんでした。もう一度受付を確認してください");
        sessionStorage.removeItem(key);
        location.href = data.url;
      }
    } catch (error) {
      message.textContent = error.message + "。画面の入力は保持しています。";
    } finally {
      busy = false;
      if (action === "generate") retryLabel(button);
      if (action !== "generate" || (!dirty && !cfg.source_changed && !cfg.readonly)) button.disabled = false;
    }
  });
})();
