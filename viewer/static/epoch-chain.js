"use strict";
// WB-WORLDGROW-001 段階5c-2: keeps the server-rendered epoch chain strip
// (viewer/epoch_view.py's strip_html()) live -- polls GET /api/epochs/current
// every 5s and updates only the summary text and the stop/continue buttons'
// hidden state. Never reloads or navigates the page on its own.
(() => {
  const STEP_LABELS = {run: "実験中", propose: "提案中", approve: "承認待ち", retire: "淘汰中", done: "完了"};
  const STATE_LABELS = {waiting: "承認待ち", stopping: "停止中", stopped: "停止", failed: "失敗", completed: "完了"};

  const get = async (url) => {
    const response = await fetch(url, {headers: {"X-WorldBloom-Client": "1"}});
    let body = null;
    try { body = await response.json(); } catch (_) { /* no body */ }
    return {ok: response.ok, body};
  };

  const post = async (url) => {
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-WorldBloom-Client": "1"},
      body: "{}",
    });
    return response.ok;
  };

  // R1 (Opus review): a failed chain names why; a running one shows how
  // stale the last tick was -- mirrors viewer/epoch_view.py's _extra_status().
  const extraStatus = (chain) => {
    if (chain.state === "failed") {
      const message = chain.error && chain.error.message;
      return message ? ` · ${message}` : "";
    }
    if (chain.state === "running" && chain.epochs[chain.epochs.length - 1].step !== "run" && typeof chain.updated_at === "number") {
      const minutes = Math.max(0, Math.floor((Date.now() / 1000 - chain.updated_at) / 60));
      return minutes === 0 ? " · 最終更新たった今" : ` · 最終更新${minutes}分前`;
    }
    return "";
  };

  const summaryText = (chain) => {
    const epochs = chain.epochs;
    const epoch = epochs[epochs.length - 1];
    const stepLabel = STATE_LABELS[chain.state] || STEP_LABELS[epoch.step] || epoch.step;
    const approved = epochs.filter((e) => e.approved_rev).length;
    const retired = epochs.reduce((n, e) => n + (e.retired || []).length, 0);
    return `エポック連鎖 ${epochs.length}/${chain.max_epochs} · 段階: ${stepLabel} · `
      + `承認 ${approved} 件・淘汰 ${retired} 件${extraStatus(chain)}`;
  };

  // R3 (Opus review): a poll answer with no chain, a foreign chain_id, or a
  // network/parse failure must never leave a stop/continue button offered
  // for data that isn't (or might not be) this strip's own chain anymore.
  const hideButtons = (strip) => {
    const stopButton = strip.querySelector("[data-epoch-stop]");
    const continueButton = strip.querySelector("[data-epoch-continue]");
    if (stopButton) stopButton.hidden = true;
    if (continueButton) continueButton.hidden = true;
  };

  const render = (strip, chain) => {
    strip.dataset.state = chain.state;
    const summary = strip.querySelector("[data-epoch-summary]");
    if (summary) summary.textContent = summaryText(chain);
    const stopButton = strip.querySelector("[data-epoch-stop]");
    const continueButton = strip.querySelector("[data-epoch-continue]");
    if (stopButton) stopButton.hidden = !(chain.state === "running" || chain.state === "waiting");
    if (continueButton) continueButton.hidden = chain.state !== "waiting";
  };

  const init = (strip) => {
    const chainId = strip.dataset.chainId;
    const poll = async () => {
      try {
        const {ok, body} = await get("/api/epochs/current");
        if (ok && body && body.chain && body.chain.chain_id === chainId) {
          render(strip, body.chain);
        } else {
          hideButtons(strip);
        }
      } catch (_) {
        hideButtons(strip);
      }
    };
    strip.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-epoch-stop],[data-epoch-continue]");
      if (!button || button.disabled) return;
      button.disabled = true;
      const action = button.hasAttribute("data-epoch-stop") ? "stop" : "continue";
      let ok = false;
      try {
        ok = await post(`/api/epochs/${encodeURIComponent(chainId)}/${action}`);
      } catch (_) {
        ok = false;
      } finally {
        button.disabled = false;
      }
      if (ok) {
        await poll();
      } else {
        const summary = strip.querySelector("[data-epoch-summary]");
        if (summary) summary.textContent = "操作に失敗しました";
      }
    });
    poll();
    setInterval(poll, 5000);
  };

  document.querySelectorAll("[data-epoch-chain]").forEach(init);
})();
