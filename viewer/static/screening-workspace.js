(() => {
  "use strict";
  const root = document.querySelector("[data-screening]");
  if (!root) return;
  root.querySelectorAll("[data-sc-navigate]").forEach(select => select.addEventListener("change", () => location.assign(select.value)));
  // Legacy candidate links use an entry fragment; preserve that identity on arrival.
  if (location.hash.startsWith("#entry-")) {
    const cid = decodeURIComponent(location.hash.slice(7));
    const target = [...root.querySelectorAll(".sc-work")].find(a => new URL(a.href).searchParams.get("candidate") === cid);
    if (target && !target.hasAttribute("aria-current")) {
      const url = new URL(target.href);
      const oid = location.pathname.match(/^\/outputs\/(out-[^/]+)$/)?.[1];
      if (oid) url.searchParams.set("output", oid);
      location.replace(url.href);
      return;
    }
  }
  const reading = root.querySelector("[data-sc-reading]");
  const progress = root.querySelector("[data-sc-progress]");
  let active = "body", fontSize = 18;
  try {
    const saved = Number(sessionStorage.getItem("wb-screening-font"));
    if (saved >= 14 && saved <= 26) fontSize = saved;
  } catch { /* Reader works even when storage is unavailable. */ }
  root.style.setProperty("--sc-font", `${fontSize}px`);
  const sizeLimits = () => root.querySelectorAll("[data-sc-size]").forEach(b => { b.disabled = Number(b.dataset.scSize) < 0 ? fontSize === 14 : fontSize === 26; });
  sizeLimits();
  const updateProgress = () => {
    if (!reading || !progress) return;
    const height = reading.scrollHeight - reading.clientHeight;
    progress.textContent = (active === "body" ? "読書位置 " : "表示位置 ") + Math.round(height > 1 ? 100 * reading.scrollTop / height : 100) + "%";
  };
  const tabs = [...root.querySelectorAll("[data-sc-tab]")];
  const positions = {};
  const activate = button => {
    positions[active] = reading.scrollTop;
    active = button.dataset.scTab;
    tabs.forEach(tab => { tab.setAttribute("aria-selected", String(tab === button)); tab.tabIndex = tab === button ? 0 : -1; });
    root.querySelectorAll("[data-sc-panel]").forEach(panel => { panel.hidden = panel.dataset.scPanel !== active; });
    reading.scrollTop = positions[active] || 0;
    updateProgress();
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", event => {
      let next;
      if (event.key === "ArrowRight") next = (i + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (i + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault(); tabs[next].focus(); activate(tabs[next]);
    });
  });
  root.querySelectorAll("[data-sc-size]").forEach(button => button.addEventListener("click", () => {
    fontSize = Math.max(14, Math.min(26, fontSize + Number(button.dataset.scSize) * 2));
    root.style.setProperty("--sc-font", `${fontSize}px`);
    sizeLimits();
    try { sessionStorage.setItem("wb-screening-font", String(fontSize)); } catch {}
    updateProgress();
  }));
  const focus = root.querySelector("[data-sc-focus]");
  const restore = () => { root.classList.remove("sc-focus"); if (focus) { focus.textContent = "集中して読む"; focus.setAttribute("aria-pressed", "false"); } updateProgress(); };
  focus?.addEventListener("click", () => {
    if (root.classList.contains("sc-focus")) return restore();
    root.classList.add("sc-focus"); root.classList.remove("sc-show-list"); focus.textContent = "一覧も表示する"; focus.setAttribute("aria-pressed", "true"); updateProgress();
  });
  root.addEventListener("keydown", event => { if (event.key === "Escape") { restore(); root.querySelectorAll("details[open]").forEach(d => { d.open = false; }); } });
  root.querySelector("[data-sc-back]")?.addEventListener("click", () => { restore(); root.classList.add("sc-show-list"); root.querySelector(".sc-work[aria-current], .sc-search input")?.focus(); });
  if (!new URL(location.href).searchParams.has("candidate") && !location.pathname.match(/^\/outputs\/out-/)) root.classList.add("sc-show-list");
  root.querySelector(".sc-work[aria-current]")?.scrollIntoView({ block: "nearest" });
  reading?.addEventListener("scroll", updateProgress, { passive: true });
  if (reading) new ResizeObserver(updateProgress).observe(reading);
  updateProgress();
  root.querySelector("[data-sc-refresh]").addEventListener("click", () => location.reload());
  // Poll only active attempts. Offer an update without moving the reader's position or draft.
  let pending = JSON.parse(root.dataset.poll || "[]");
  const notice = root.querySelector("[data-sc-update]");
  const poll = async () => {
    if (document.hidden) { setTimeout(poll, 4000); return; }
    let failed = false, changed = false;
    for (const item of pending) {
      try {
        const response = await fetch(`/api/outputs/${encodeURIComponent(item.output_id)}`, { headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("connection");
        const data = await response.json();
        const entry = (data.entries || []).find(e => e.candidate_id === item.candidate_id);
        if (!entry) throw new Error("missing entry");
        if (entry.status !== item.status || !data.job_state || ["succeeded", "partial", "failed", "cancelled", "interrupted"].includes(data.job_state)) changed = true;
      } catch { failed = true; }
    }
    notice.hidden = !failed && !changed;
    notice.querySelector("span").textContent = failed ? "生成状況を確認できません。表示中の本文は引き続き読めます。" : "生成状況が更新されました。表示を更新すると最新の記録を確認できます。";
    if (!changed || failed) setTimeout(poll, 4000);
  };
  if (pending.length) setTimeout(poll, 2000);
})();
