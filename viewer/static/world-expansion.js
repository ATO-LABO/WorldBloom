"use strict";
(() => {
  const post = async (url, payload) => {
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-WorldBloom-Client": "1"},
      body: JSON.stringify(payload),
    });
    let body = null;
    try { body = await response.json(); } catch (_) { /* no body */ }
    return {ok: response.ok, status: response.status, body};
  };

  const get = async (url) => {
    const response = await fetch(url, {headers: {"X-WorldBloom-Client": "1"}});
    let body = null;
    try { body = await response.json(); } catch (_) { /* no body */ }
    return {ok: response.ok, status: response.status, body};
  };

  const reload = () => {
    if (document.querySelector("[data-run-workspace]")) {
      const url = new URL(location.href);
      if (!url.searchParams.has("tab")) {
        url.searchParams.set("tab", "demand");
        history.replaceState(null, "", url);
      }
    }
    location.reload();
  };

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-patch-action]");
    // V3 (viewer review): the browser already withholds click from a
    // disabled button, but a delegated document-level listener still gets
    // the bubbled event in some browsers/cases -- check explicitly too.
    if (!button || button.disabled) return;
    const action = button.dataset.patchAction;
    if (action === "propose" || action === "check") return; // handled below, by initJobPanel
    const world = button.dataset.world;
    const container = button.closest("[data-patch-card]") || button.closest("li") || button.parentElement;
    const messageEl = container ? container.querySelector("[data-patch-message]") : null;
    const setMessage = (text) => { if (messageEl) messageEl.textContent = text; else if (text) alert(text); };

    let url, payload;
    if (action === "approve") {
      const textarea = container ? container.querySelector("[data-approve-reason]") : null;
      const reason = (textarea ? textarea.value : "").trim();
      if (reason.length < 10) {
        setMessage("承認理由を10文字以上入力してください。");
        return;
      }
      url = `/api/worlds/${encodeURIComponent(world)}/patches/${encodeURIComponent(button.dataset.patch)}/approve`;
      payload = {reason, seen: {patch_sha256: button.dataset.patchSha, gate_sha256: button.dataset.gateSha}};
    } else if (action === "reject") {
      if (!confirm("この提案を却下しますか？ 却下した提案は画面からは元に戻せません。")) return;
      url = `/api/worlds/${encodeURIComponent(world)}/patches/${encodeURIComponent(button.dataset.patch)}/reject`;
      payload = {seen: {patch_sha256: button.dataset.patchSha, gate_sha256: button.dataset.gateSha || null}};
    } else if (action === "reopen") {
      // R2 (viewer review): reopen() restores every approved revision at
      // once (later patches were gated against a world that already had
      // the earlier ones applied) -- the confirmation must say so, not
      // imply a single-item undo.
      const count = button.dataset.count || "";
      if (!confirm(`承認済みの拡張 ${count} 件をすべて提案中へ戻します。戻したあとは検査のやり直しが必要です。よろしいですか？`)) return;
      url = `/api/worlds/${encodeURIComponent(world)}/patches/reopen`;
      payload = {seen: {head: button.dataset.head}};
    } else if (action === "retire") {
      // WB-WORLDGROW-001 段階5a: yaml/gate は消えない（墓標リビジョン）が、
      // 以後は適用中パッチから外れる -- 確認だけは取る。
      const textarea = container ? container.querySelector("[data-retire-reason]") : null;
      const reason = (textarea ? textarea.value : "").trim();
      if (reason.length < 10) {
        setMessage("枯らす理由を10文字以上入力してください。");
        return;
      }
      if (!confirm("このパッチを枯らします（元に戻すには reopen が必要です）。よろしいですか？")) return;
      url = `/api/worlds/${encodeURIComponent(world)}/patches/${encodeURIComponent(button.dataset.patch)}/retire`;
      payload = {reason, experiment: button.dataset.experiment, seen: {head: button.dataset.head}};
    } else {
      return;
    }

    button.disabled = true;
    setMessage("");
    try {
      const result = await post(url, payload);
      if (!result.ok) {
        let text = (result.body && result.body.message) || "処理に失敗しました。";
        if (result.status === 409) text += "（再読み込みしてください）";
        setMessage(text);
        button.disabled = false;
        return;
      }
      reload();
    } catch (_) {
      setMessage("通信に失敗しました。");
      button.disabled = false;
    }
  });

  // --------------------------------------------------------------------
  // World-patch job panel (propose/check + progress) -- WB-WORLDGROW-001
  // 段階3b-3. Only present on the run's demand tab (viewer/run_workspace.py's
  // _proposals_html); the world screen (viewer/world_prototype.py) has no
  // [data-patch-job] and never runs any of this.
  // --------------------------------------------------------------------
  const panel = document.querySelector("[data-patch-job]");
  if (!panel) return;

  const TERMINAL = new Set(["succeeded", "partial", "failed", "cancelled", "interrupted"]);
  const runName = panel.dataset.run;
  const statusEl = panel.querySelector('[role="status"]');
  const cancelBtn = panel.querySelector("[data-patch-job-cancel]");

  let pollTimer = null;
  let currentJobId = null;
  let trackedStart = null; // ms, local fallback when the job has no created_at
  let cancelRequested = false;
  let foreignJob = false; // an active job that isn't our own propose/check
  let pollFailures = 0;

  // Disable only what is enabled, and re-enable only what this did: a proposal
  // that cannot be approved is rendered with its approve button disabled, and
  // a finished job must not hand that button back.
  const setActionsDisabled = (disabled) => {
    const selector = disabled ? "[data-patch-action]:not([disabled])" : "[data-patch-action][data-patch-job-disabled]";
    document.querySelectorAll(selector).forEach((el) => {
      if (disabled) el.dataset.patchJobDisabled = "1"; else delete el.dataset.patchJobDisabled;
      el.disabled = disabled;
    });
  };
  const showPanel = (text) => { panel.hidden = false; if (statusEl) statusEl.textContent = text; };
  const hidePanel = () => { panel.hidden = true; };
  const stopPolling = () => { clearTimeout(pollTimer); pollTimer = null; };

  const requestId = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return "req-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  };

  const stepText = (progress) => {
    const step = (progress && progress.step) || "queued";
    if (step === "queued" || step === "preparing") return "準備しています";
    if (step === "generate") {
      const attempt = progress.attempt, attempts = progress.attempts;
      const n = attempt != null && attempts != null ? `${attempt}/${attempts} 回目。` : "";
      return `拡張を考えています（${n}1 回に 6〜11 分かかります）`;
    }
    if (step === "trial") return "試走しています（探索用の seed）";
    if (step === "holdout") return "承認用の seed で検査しています";
    return "処理しています";
  };

  const elapsedText = (job) => {
    const startSeconds = typeof job?.created_at === "number" ? job.created_at
      : trackedStart != null ? trackedStart / 1000 : null;
    if (startSeconds == null) return "";
    const minutes = Math.floor((Date.now() / 1000 - startSeconds) / 60);
    return minutes >= 1 ? `開始から ${minutes} 分` : "開始から 1 分未満";
  };

  const pollJob = async (jobId) => {
    if (currentJobId !== jobId) { currentJobId = jobId; trackedStart = Date.now(); pollFailures = 0; }
    let result = null;
    try { result = await get(`/api/jobs/${encodeURIComponent(jobId)}`); } catch (_) { /* counted below */ }
    if (!result || !result.ok || !result.body) {
      // The job runs for 10-30 minutes on the server whatever this page sees:
      // one failed poll must not drop the panel and hand the buttons back.
      pollFailures += 1;
      if (pollFailures >= 3) {
        showPanel("進み具合を取得できません。処理は続いている可能性があります。しばらくして再読み込みしてください。");
      }
      pollTimer = setTimeout(() => pollJob(jobId), 3000);
      return;
    }
    pollFailures = 0;
    const job = result.body;
    if (!TERMINAL.has(job.state)) {
      setActionsDisabled(true);
      if (cancelBtn) cancelBtn.hidden = foreignJob;
      if (foreignJob) {
        showPanel("ほかの処理（実行・生成）が動いています。終わるまで提案・承認はできません。");
      } else if (cancelRequested || job.cancel_requested_at) {
        showPanel("停止しています…");
      } else {
        showPanel(`${stepText(job.progress)} ／ ${elapsedText(job)}`);
      }
      pollTimer = setTimeout(() => pollJob(jobId), 3000);
      return;
    }
    // Terminal.
    setActionsDisabled(false);
    stopPolling();
    if (cancelBtn) cancelBtn.hidden = true;
    if (foreignJob) { hidePanel(); return; }
    if (job.state === "succeeded") { reload(); return; }
    if (job.state === "failed") showPanel((job.progress && job.progress.message) || "提案できませんでした");
    else if (job.state === "cancelled") showPanel("停止しました");
    else showPanel("中断されました");
  };

  if (cancelBtn) {
    cancelBtn.addEventListener("click", async () => {
      if (!currentJobId || foreignJob) return;
      if (!confirm("この処理を停止しますか？")) return;
      cancelRequested = true;
      cancelBtn.disabled = true;
      showPanel("停止しています…");
      let stopped = null;
      try { stopped = await post(`/api/jobs/${encodeURIComponent(currentJobId)}/cancel`, {}); } catch (_) { /* below */ }
      if (!stopped || !stopped.ok) {
        cancelRequested = false;
        showPanel((stopped && stopped.body && stopped.body.message) || "停止できませんでした。もう一度試してください。");
      }
      cancelBtn.disabled = false;
    });
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-patch-action="propose"],[data-patch-action="check"]');
    if (!button || button.disabled) return;
    const action = button.dataset.patchAction;
    const run = button.dataset.run;
    const container = button.closest("[data-patch-card]") || button.closest("li") || button.parentElement;
    const messageEl = container ? container.querySelector("[data-patch-message]") : null;
    // A trigger row has no message slot of its own; the job panel sits right
    // above the list, so the reason goes there instead of into an alert().
    const setMessage = (text) => {
      if (messageEl) messageEl.textContent = text;
      else if (text) { if (cancelBtn) cancelBtn.hidden = true; showPanel(text); }
    };

    const payload = {request_id: requestId(), action};
    if (action === "propose") payload.trigger = Number(button.dataset.trigger);
    else payload.patch_id = button.dataset.patch;

    setActionsDisabled(true);
    setMessage("");
    let result;
    try {
      result = await post(`/api/runs/${encodeURIComponent(run)}/world-patch`, payload);
    } catch (_) {
      setActionsDisabled(false);
      setMessage("通信に失敗しました。");
      return;
    }
    if (!result.ok) {
      setActionsDisabled(false);
      let text = (result.body && result.body.message) || "処理に失敗しました。";
      if (result.status === 409) text += "（ほかの処理が終わってからもう一度試してください）";
      setMessage(text);
      return;
    }
    foreignJob = false;
    cancelRequested = false;
    stopPolling(); // defensive: drop any stale timer from a previous job before tracking this one
    pollJob(result.body.job_id);
  });

  // Reconnect after leaving/returning to the page: find any job still
  // running and either resume polling it (our own world_patch job for this
  // run) or just report that something else is busy.
  (async () => {
    let result;
    try { result = await get("/api/jobs"); } catch (_) { return; } // network hiccup -- nothing to reconcile yet
    if (!result.ok) return; // includes 503 (no job management)
    const jobs = (result.body && result.body.jobs) || [];
    const active = jobs.find((j) => !TERMINAL.has(j.state));
    // A click may have started tracking a job while this scan was in flight.
    if (!active || currentJobId) return;
    foreignJob = !(active.kind === "world_patch" && active.run_id === runName);
    pollJob(active.job_id);
  })();
})();
