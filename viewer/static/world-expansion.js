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
      if (!confirm("この提案を却下しますか？")) return;
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
})();
