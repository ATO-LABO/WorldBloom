"use strict";

(() => {
  const toast = document.getElementById("toast");
  let toastTimer = null;

  const notify = (message) => {
    if (!toast) {
      return;
    }
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(
      () => toast.classList.remove("visible"),
      2400
    );
  };

  const desiredSelection = (control) => {
    if (control instanceof HTMLInputElement) {
      return control.checked;
    }
    return control.getAttribute("aria-pressed") !== "true";
  };

  const renderSelection = (control, selected) => {
    if (control instanceof HTMLInputElement) {
      control.checked = selected;
    } else {
      control.setAttribute("aria-pressed", String(selected));
      control.textContent = selected ? "★" : "☆";
    }
  };

  const saveSelection = async (control) => {
    const endpoint = control.dataset.endpoint;
    const cell = control.dataset.cell;
    if (!endpoint || !cell) {
      return;
    }
    const previous = !desiredSelection(control);
    const selected = desiredSelection(control);
    control.disabled = true;
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({cell, selected})
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const result = await response.json();
      document
        .querySelectorAll(".selection-toggle")
        .forEach((candidate) => {
          if (candidate.dataset.cell === cell) {
            renderSelection(candidate, Boolean(result.selected));
          }
        });
      notify("selection.json に保存しました");
    } catch (error) {
      renderSelection(control, previous);
      notify(`保存に失敗しました: ${error.message}`);
    } finally {
      control.disabled = false;
    }
  };

  document.querySelectorAll(".selection-toggle").forEach((control) => {
    const eventName = control instanceof HTMLInputElement
      ? "change"
      : "click";
    control.addEventListener(eventName, () => saveSelection(control));
  });

  const toggleSeries = (index) => {
    document
      .querySelectorAll(`[data-series="${index}"]`)
      .forEach((element) => element.classList.toggle("off"));
  };

  document.querySelectorAll(".legend-item").forEach((legend) => {
    legend.addEventListener(
      "click",
      () => toggleSeries(legend.dataset.series)
    );
    legend.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleSeries(legend.dataset.series);
      }
    });
  });

  const highlightDay = (day, active) => {
    document
      .querySelectorAll(`[data-day="${day}"]`)
      .forEach((element) => element.classList.toggle("active", active));
  };

  document.querySelectorAll(".day-mark, .day").forEach((element) => {
    element.addEventListener(
      "mouseenter",
      () => highlightDay(element.dataset.day, true)
    );
    element.addEventListener(
      "mouseleave",
      () => highlightDay(element.dataset.day, false)
    );
  });

  // WB-UI-018/021: character table rows and relation-graph nodes open the
  // matching stat-sheet <dialog>.
  const openSheet = (trigger) => {
    const dialog = document.getElementById(trigger.dataset.sheet);
    if (dialog && typeof dialog.showModal === "function" && !dialog.open) {
      dialog.showModal();
    }
  };

  document.querySelectorAll("tr[data-sheet], .relation-graph .node[data-sheet]").forEach((trigger) => {
    trigger.addEventListener("click", () => openSheet(trigger));
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openSheet(trigger);
      }
    });
  });

  // WB-UI-021: hovering (or focusing) a node lights it and the edges that
  // touch it; everything else in the graph dims.
  document.querySelectorAll(".relation-graph").forEach((graph) => {
    const light = (node, on) => {
      const id = node.dataset.sheet;
      graph.classList.toggle("has-lit", on);
      node.classList.toggle("lit", on);
      graph.querySelectorAll("line").forEach((line) => {
        const touches = line.dataset.a === id || line.dataset.b === id;
        line.classList.toggle("lit", on && touches);
        if (on && touches) {
          graph.querySelector(`.node[data-sheet="${line.dataset.a === id ? line.dataset.b : line.dataset.a}"]`)
            ?.classList.add("lit-peer");
        }
      });
      if (!on) {
        graph.querySelectorAll(".lit-peer").forEach((peer) => peer.classList.remove("lit-peer"));
      }
    };
    graph.querySelectorAll(".node[data-sheet]").forEach((node) => {
      node.addEventListener("mouseenter", () => light(node, true));
      node.addEventListener("mouseleave", () => light(node, false));
      node.addEventListener("focus", () => light(node, true));
      node.addEventListener("blur", () => light(node, false));
    });
  });

  document.querySelectorAll("dialog.sheet-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      // The dialog itself has no padding, so a click that lands on it (not
      // on a descendant) is a backdrop click.
      if (event.target === dialog || event.target.closest("[data-close-dialog]")) {
        dialog.close();
      }
    });
  });

  const deleteRun = async (button) => {
    const endpoint = button.dataset.endpoint;
    const name = button.dataset.run;
    if (!endpoint || !name) {
      return;
    }
    if (!window.confirm(`実験「${name}」を完全に削除します。関連するジョブ記録と上映出力も消え、元に戻せません。よろしいですか？`)) {
      return;
    }
    button.disabled = true;
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"X-WorldBloom-Client": "1"}
      });
      if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try {
          const body = await response.json();
          if (body && body.message) {
            message = body.message;
          }
        } catch (error) {
          // response body wasn't JSON; keep the plain HTTP status message.
        }
        throw new Error(message);
      }
      const row = button.closest(".progress-row");
      if (row) {
        row.remove();
      }
      notify("削除しました");
      const tabLabel = document.querySelector('label[for="tab-world-5"]');
      if (tabLabel) {
        tabLabel.textContent = tabLabel.textContent.replace(
          /\((\d+)\)/,
          (_match, count) => `(${Number(count) - 1})`
        );
      }
    } catch (error) {
      notify(`削除に失敗しました: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  };

  document.querySelectorAll(".run-delete").forEach((button) => {
    button.addEventListener("click", () => deleteRun(button));
  });

  const generateReaderSummary = async (button) => {
    const endpoint = button.dataset.endpoint;
    if (!endpoint) {
      return;
    }
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "生成中…";
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"X-WorldBloom-Client": "1"}
      });
      if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try {
          const body = await response.json();
          if (body && body.message) {
            message = body.message;
          }
        } catch (error) {
          // response body wasn't JSON; keep the plain HTTP status message.
        }
        throw new Error(message);
      }
      window.location.reload();
    } catch (error) {
      notify(`生成に失敗しました: ${error.message}`);
      button.disabled = false;
      button.textContent = label;
    }
  };

  document.querySelectorAll(".reader-generate").forEach((button) => {
    button.addEventListener("click", () => generateReaderSummary(button));
  });

  // WB-UI-028: approved candidate workspace and detail tabs.
  const candidateRoot = document.querySelector('[data-wb="candidates"]');
  const candidateRows = Array.from(document.querySelectorAll(".candidate-list-row"));
  const inspector = document.querySelector(".candidate-inspector-final");
  const stateLabels = { adopted: "採用", held: "保留", rejected: "除外", unclassified: "未分類" };
  let activeCandidateRow = null;

  const updateCandidateCounts = () => {
    if (!candidateRoot) return;
    const adopted = candidateRows.filter((row) => row.querySelector('[data-field="state"]')?.value === "adopted").length;
    const generated = candidateRows.filter((row) => row.querySelector('input[name="candidate"]')?.checked).length;
    inspector?.querySelector("[data-adopted-count]")?.replaceChildren(String(adopted));
    inspector?.querySelector("[data-generate-count]")?.replaceChildren(`生成対象 ${generated}件`);
  };

  const inspectCandidate = (row) => {
    if (!inspector || !row) return;
    activeCandidateRow = row;
    candidateRows.forEach((candidate) => candidate.classList.toggle("is-inspected", candidate === row));
    inspector.querySelector("[data-inspector-id]").textContent = row.dataset.candidateLabel || "候補";
    inspector.querySelector("[data-inspector-title]").textContent = row.dataset.title || "名称なし";
    inspector.querySelector("[data-inspector-synopsis]").textContent = row.dataset.synopsis || "あらすじはまだ生成されていません。";
    inspector.querySelectorAll("[data-inspector-quality]").forEach((node) => { node.textContent = row.dataset.quality || "—"; });
    inspector.querySelector("[data-inspector-reached]").textContent = row.dataset.reached || "—";
    inspector.querySelector("[data-inspector-generation]").textContent = row.dataset.generation || "—";
    inspector.querySelector("[data-inspector-seed]").textContent = row.dataset.seed || "—";
    const state = row.querySelector('[data-field="state"]')?.value || "unclassified";
    const stateLabel = inspector.querySelector("[data-inspector-state-label]");
    stateLabel.textContent = stateLabels[state] || state;
    stateLabel.className = `state-badge state-sel-${state}`;
    inspector.querySelector("[data-inspector-state]").value = state;
    inspector.querySelector("[data-inspector-note]").value = row.querySelector('[data-field="note"]')?.value || "";
    const primary = inspector.querySelector("[data-inspector-primary]");
    primary.href = row.dataset.primaryHref || "#candidate-table";
    primary.textContent = row.dataset.primaryLabel || "詳しく読む";
    inspector.querySelector("[data-inspector-save-status]").textContent = row.querySelector("[data-save-status]")?.textContent || "";
    updateCandidateCounts();
  };

  candidateRows.forEach((row) => {
    row.querySelector(".candidate-open")?.addEventListener("click", () => inspectCandidate(row));
    row.querySelector('[data-field="state"]')?.addEventListener("change", () => {
      row.dataset.state = row.querySelector('[data-field="state"]').value;
      if (activeCandidateRow === row) inspectCandidate(row);
      updateCandidateCounts();
    });
    row.querySelector('input[name="candidate"]')?.addEventListener("change", updateCandidateCounts);
    const statusNode = row.querySelector("[data-save-status]");
    if (statusNode && inspector) {
      new MutationObserver(() => {
        if (activeCandidateRow === row) inspector.querySelector("[data-inspector-save-status]").textContent = statusNode.textContent;
      }).observe(statusNode, { childList: true, characterData: true, subtree: true });
    }
  });

  inspector?.querySelector("[data-inspector-state]")?.addEventListener("change", (event) => {
    const select = activeCandidateRow?.querySelector('[data-field="state"]');
    if (!select) return;
    select.value = event.currentTarget.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  inspector?.querySelector("[data-inspector-note]")?.addEventListener("input", (event) => {
    const input = activeCandidateRow?.querySelector('[data-field="note"]');
    if (input) input.value = event.currentTarget.value;
  });
  inspector?.querySelector("[data-inspector-save]")?.addEventListener("click", () => activeCandidateRow?.querySelector('[data-action="save-candidate"]')?.click());
  inspector?.querySelector("[data-inspector-adopt]")?.addEventListener("click", () => {
    const select = activeCandidateRow?.querySelector('[data-field="state"]');
    if (!select) return;
    select.value = "adopted";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    activeCandidateRow.querySelector('[data-action="save-candidate"]')?.click();
  });
  inspector?.querySelector("[data-inspector-detail-toggle]")?.addEventListener("click", () => activeCandidateRow?.querySelector(".row-toggle")?.click());
  inspectCandidate(candidateRows[0]);

  document.querySelectorAll(".candidate-inspector-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      inspector.querySelectorAll(".candidate-inspector-tab").forEach((item) => {
        const active = item === tab;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
      });
      inspector.querySelectorAll("[data-inspector-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.inspectorPanel !== tab.dataset.inspectorTab;
      });
    });
  });

  document.querySelectorAll(".candidate-detail-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const shell = tab.closest(".candidate-detail-shell");
      if (!shell) return;
      shell.querySelectorAll(".candidate-detail-tab").forEach((item) => {
        const active = item === tab;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
      });
      shell.querySelectorAll(".candidate-detail-panel").forEach((panel) => {
        const active = panel.dataset.detailPanel === tab.dataset.detailTab;
        panel.hidden = !active;
        panel.classList.toggle("is-active", active);
        if (active) panel.scrollTop = 0;
      });
    });
  });

  // WB-UI-028: workspace keyboard and scroll continuity.
  document.querySelectorAll(".candidate-detail-tabs").forEach((tabs) => {
    tabs.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const items = Array.from(tabs.querySelectorAll(".candidate-detail-tab"));
      const current = items.indexOf(document.activeElement);
      if (current < 0) return;
      event.preventDefault();
      let next = current;
      if (event.key === "ArrowLeft") next = (current - 1 + items.length) % items.length;
      if (event.key === "ArrowRight") next = (current + 1) % items.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = items.length - 1;
      items[next].focus();
      items[next].click();
    });
  });

  const routeKey = `wb-scroll:${window.location.pathname}${window.location.search}`;
  const scrollRegion = document.querySelector(".page-content");
  if (scrollRegion && !document.querySelector(".candidate-workspace, .candidate-detail-workspace")) {
    const saved = Number(sessionStorage.getItem(routeKey));
    if (Number.isFinite(saved) && saved > 0) scrollRegion.scrollTop = saved;
    window.addEventListener("pagehide", () => sessionStorage.setItem(routeKey, String(scrollRegion.scrollTop)));
  }

})();


// WB-UI-028: approved GA and configuration workspace enhancements.
document.addEventListener("DOMContentLoaded", () => {
  const runLayout = document.querySelector(".run-layout");
  if (runLayout && !runLayout.dataset.wbUiFinal) {
    runLayout.dataset.wbUiFinal = "true";
    const panels = runLayout.querySelectorAll(".run-vessel > .vbox");
    panels[0]?.classList.add("run-progress-panel");
    panels[1]?.classList.add("run-map-panel");
    panels[2]?.classList.add("run-next-panel");
    const llmNote = runLayout.querySelector(".run-prep > .muted");
    llmNote?.classList.add("run-llm-contract");
  }

  const form = document.querySelector(".cfg-form");
  if (!form || form.dataset.wbUiFinal) return;
  form.dataset.wbUiFinal = "true";

  const actions = form.querySelector(".form-actions");
  if (!actions) return;

  const intro = document.createElement("div");
  intro.className = "config-support-intro";
  const kicker = document.createElement("span");
  kicker.className = "config-support-kicker";
  kicker.textContent = "Execution contract";
  const title = document.createElement("h2");
  title.textContent = "この内容を固定して実行";
  const introText = document.createElement("p");
  introText.textContent = "保存した設定版を実行結果に結び付けます。後から候補を開いても、使った世界・規模・seedを追跡できます。";
  intro.append(kicker, title, introText);

  const facts = document.createElement("dl");
  facts.className = "config-support-facts";
  const factNames = [
    ["world", "世界"],
    ["template", "ジャンル"],
    ["scale", "探索規模"],
    ["runs", "予定ラン"],
    ["llm", "LLM利用"]
  ];
  const factValues = {};
  for (const [key, label] of factNames) {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = label;
    dd.dataset.configFact = key;
    row.append(dt, dd);
    facts.append(row);
    factValues[key] = dd;
  }

  const contract = document.createElement("p");
  contract.className = "config-support-contract";
  contract.textContent = "GA実行ではLLMを呼びません。文章生成の利用量と上限は、Sifting後の生成工程で別に確認します。";

  actions.prepend(intro, facts, contract);

  const valueOf = (name, fallback = "—") => {
    const field = form.elements.namedItem(name);
    if (!field || !("value" in field)) return fallback;
    const value = String(field.value || "").trim();
    return value || fallback;
  };
  const numberOf = (name) => {
    const value = Number(valueOf(name, "0"));
    return Number.isFinite(value) ? value : 0;
  };
  const updateFacts = () => {
    const generations = numberOf("evolution.generations");
    const population = numberOf("evolution.population");
    const seeds = numberOf("evolution.seeds");
    const runs = generations * population * seeds;
    factValues.world.textContent = valueOf("project_id");
    factValues.template.textContent = valueOf("template_id");
    factValues.scale.textContent = generations + "世代 × " + population + "個体 × " + seeds + "seed";
    factValues.runs.textContent = runs.toLocaleString("ja-JP");
    factValues.llm.textContent = "GAでは使用しない";
  };
  form.addEventListener("input", updateFacts);
  form.addEventListener("change", updateFacts);
  updateFacts();
});


// WB-UI-028: step 10 review corrections.
document.addEventListener("DOMContentLoaded", () => {
  const runPrep = document.querySelector(".run-prep");
  if (runPrep && !runPrep.querySelector(".run-prep-scroll")) {
    const startButton = [...runPrep.querySelectorAll("button")]
      .find((button) => button.textContent.includes("GA を回す"));
    const startBox = startButton?.closest("form")?.parentElement;
    const llmNote = runPrep.querySelector(".run-llm-contract");
    if (startBox) {
      const scroll = document.createElement("div");
      scroll.className = "run-prep-scroll";
      const footer = document.createElement("div");
      footer.className = "run-prep-footer";
      [...runPrep.children]
        .filter((child) => child !== startBox && child !== llmNote)
        .forEach((child) => scroll.append(child));
      footer.append(startBox);
      if (llmNote) footer.append(llmNote);
      runPrep.append(scroll, footer);
    }
  }

  const form = document.querySelector(".cfg-form");
  const actions = form?.querySelector(":scope > .form-actions");
  if (form && actions && !form.querySelector(":scope > .cfg-form-fields")) {
    const fields = document.createElement("div");
    fields.className = "cfg-form-fields";
    [...form.children]
      .filter((child) => child !== actions)
      .forEach((child) => fields.append(child));
    form.prepend(fields);
  }
});


// WB-UI-027: reading, selection, resume, context, and generation flow.
document.addEventListener("DOMContentLoaded", () => {
  const worldSelect = document.querySelector('select[aria-label="世界"], header select, .topbar select');
  const worldOptions = worldSelect
    ? [...worldSelect.options].map((option) => option.value).filter(Boolean).sort((a, b) => b.length - a.length)
    : [];

  const inferWorld = () => {
    const query = new URLSearchParams(location.search);
    const explicit = query.get("project") || query.get("world");
    if (explicit && worldOptions.includes(explicit)) return explicit;
    const worldMatch = location.pathname.match(/^\/worlds\/([^/]+)/);
    if (worldMatch && worldOptions.includes(decodeURIComponent(worldMatch[1]))) {
      return decodeURIComponent(worldMatch[1]);
    }
    const source = [location.pathname, location.search, document.querySelector("header")?.textContent || "", document.querySelector("h1")?.textContent || ""].join(" ").toLowerCase();
    return worldOptions.find((value) => {
      const lower = value.toLowerCase();
      return source.includes("-" + lower) || source.includes("=" + lower) || source.includes("/" + lower);
    }) || "";
  };

  const inferredWorld = inferWorld();
  if (worldSelect && inferredWorld && !worldSelect.value) {
    worldSelect.value = inferredWorld;
    worldSelect.dataset.contextRestored = "true";
  }

  const createRoleGuide = () => {
    const guide = document.createElement("div");
    guide.className = "wb-role-guide";
    guide.setAttribute("role", "note");
    const items = [
      ["比較に追加", "格子で2〜4候補を並べる"],
      ["あらすじ対象", "チェックした候補だけ要約する"],
      ["本文に採用", "採用済み候補を文章化する"]
    ];
    for (const [title, description] of items) {
      const item = document.createElement("div");
      const strong = document.createElement("strong");
      const span = document.createElement("span");
      strong.textContent = title;
      span.textContent = description;
      item.append(strong, span);
      guide.append(item);
    }
    return guide;
  };

  const candidateWorkspace = document.querySelector(".candidate-workspace");
  if (candidateWorkspace) {
    const candidateRegion = document.querySelector('[aria-label="候補一覧"]');
    if (candidateRegion && !candidateRegion.querySelector(".wb-role-guide")) {
      const toolbar = candidateRegion.querySelector("nav, .candidate-list-toolbar, form");
      if (toolbar) toolbar.insertAdjacentElement("afterend", createRoleGuide());
      else candidateRegion.prepend(createRoleGuide());
    }

    const checks = [...document.querySelectorAll('.candidate-generate-check input[name="candidate"]')];
    for (const check of checks) {
      const label = check.closest("label");
      const visible = label?.querySelector("span");
      if (visible) visible.textContent = "あらすじ対象";
      check.setAttribute("aria-label", check.getAttribute("aria-label")?.replace("生成対象", "あらすじ生成の対象") || "あらすじ生成の対象にする");
    }

    const form = document.querySelector("#generate-form");
    const synopsisButton = form?.querySelector('button[name="kind"][value="synopsize"]');
    const proseButton = form?.querySelector('button[name="kind"][value="narrate"]');
    const generateCount = document.querySelector("[data-generate-count]");
    const adoptedCount = Number(document.querySelector("[data-adopted-count]")?.textContent || 0);
    const adoptButton = document.querySelector("[data-inspector-adopt]");
    if (adoptButton) adoptButton.textContent = "この候補を本文に採用";

    const updateCandidateTargets = () => {
      const count = checks.filter((check) => check.checked).length;
      if (generateCount) generateCount.textContent = "あらすじ対象 " + count + "件";
      if (synopsisButton) synopsisButton.textContent = "選んだ" + count + "件のあらすじを生成";
      if (proseButton) proseButton.textContent = "採用した" + adoptedCount + "件の本文を生成";
    };
    checks.forEach((check) => check.addEventListener("change", updateCandidateTargets));
    updateCandidateTargets();
  }

  const gridChecks = [...document.querySelectorAll('input[type="checkbox"][name="cell"]')];
  if (gridChecks.length) {
    for (const check of gridChecks) {
      const label = check.closest("label");
      if (label) {
        [...label.childNodes]
          .filter((node) => node.nodeType === Node.TEXT_NODE)
          .forEach((node) => { node.textContent = " 比較に追加"; });
        check.setAttribute("aria-label", (check.value || "候補") + " を比較に追加");
      }
    }
    const compareButton = [...document.querySelectorAll("button")].find((button) => button.textContent.includes("比較"));
    const updateCompare = () => {
      const count = gridChecks.filter((check) => check.checked).length;
      if (compareButton) compareButton.textContent = "選んだ" + count + "件を比較";
    };
    gridChecks.forEach((check) => check.addEventListener("change", updateCompare));
    updateCompare();

    const nextLink = [...document.querySelectorAll("a")].find((link) => link.textContent.includes("次: 上映を生成する"));
    const candidateLink = [...document.querySelectorAll("a")].find((link) => /\/runs\/[^/]+\/candidates$/.test(link.getAttribute("href") || ""));
    if (nextLink && candidateLink) {
      const candidateHref = candidateLink.getAttribute("href");
      nextLink.setAttribute("href", candidateHref.replace(/\/candidates$/, "/generate?kind=narrate"));
      nextLink.textContent = "次: 採用した候補の本文を生成 →";
      fetch(candidateHref)
        .then((response) => response.text())
        .then((html) => {
          const doc = new DOMParser().parseFromString(html, "text/html");
          const count = Number(doc.querySelector("[data-adopted-count]")?.textContent || 0);
          if (count > 0) nextLink.textContent = "次: 採用した" + count + "件の本文を生成 →";
        })
        .catch(() => {});
    }
  }

  const enhanceHome = async () => {
    if (location.pathname !== "/" || document.querySelector(".wb-resume")) return;
    const main = document.querySelector("main");
    if (!main) return;
    try {
      const response = await fetch("/history");
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const rows = [...doc.querySelectorAll("tbody tr")].filter((row) => row.querySelector('a[href*="/candidates"]'));
      const row = rows.at(-1);
      if (!row) return;
      const candidateLink = row.querySelector('a[href*="/candidates"]');
      const resultLink = row.querySelector('a[href^="/exp/"]');
      const runMatch = candidateLink.getAttribute("href").match(/^\/runs\/([^/]+)\/candidates/);
      const runId = runMatch ? runMatch[1] : "";
      const experiment = row.querySelector("td")?.textContent.trim().replace(/\s*\(legacy-[^)]+\)\s*$/, "") || "前回の実験";

      const section = document.createElement("section");
      section.className = "wb-resume";
      const copy = document.createElement("div");
      const kicker = document.createElement("span");
      kicker.className = "wb-resume-kicker";
      kicker.textContent = "CONTINUE";
      const title = document.createElement("h2");
      title.textContent = "前回の続き";
      const description = document.createElement("p");
      description.textContent = experiment + " の候補選びや本文確認へ戻れます。";
      copy.append(kicker, title, description);

      const actions = document.createElement("div");
      actions.className = "wb-resume-actions";
      const candidateAction = document.createElement("a");
      candidateAction.href = candidateLink.getAttribute("href");
      candidateAction.textContent = "候補を選ぶ";
      const outputAction = document.createElement("a");
      outputAction.href = runId ? "/outputs?run=" + encodeURIComponent(runId) : "/outputs";
      outputAction.textContent = "生成済みの本文を読む";
      const historyAction = document.createElement("a");
      historyAction.href = "/history";
      historyAction.textContent = "すべての履歴";
      actions.append(candidateAction, outputAction, historyAction);
      section.append(copy, actions);
      main.prepend(section);

      try {
        const candidateResponse = await fetch(candidateLink.getAttribute("href"));
        const candidateDoc = new DOMParser().parseFromString(await candidateResponse.text(), "text/html");
        const candidateCount = candidateDoc.querySelectorAll('tr[data-candidate-id]').length;
        if (candidateCount) candidateAction.textContent = "候補を選ぶ (" + candidateCount + ")";
      } catch (_) {}

      const worldCards = [...document.querySelectorAll('article')];
      const worldIds = worldCards
        .map((card) => card.querySelector('a[href^="/worlds/"]')?.getAttribute("href")?.split("/").pop())
        .filter(Boolean)
        .sort((a, b) => b.length - a.length);
      const worldId = worldIds.find((id) => experiment.toLowerCase().includes(id.toLowerCase()));
      if (worldId) {
        const card = worldCards.find((item) => item.querySelector('a[href="/worlds/' + worldId + '"]'));
        if (card && !card.querySelector(".wb-world-resume-link")) {
          const link = document.createElement("a");
          link.className = "wb-world-resume-link";
          link.href = candidateLink.getAttribute("href");
          link.textContent = candidateAction.textContent;
          card.append(link);
        }
      }
      if (resultLink) section.dataset.resultHref = resultLink.getAttribute("href");
    } catch (_) {}
  };
  enhanceHome();

  const endingField = document.querySelector('input[name="evolution.target_ending"]');
  if (endingField && !document.querySelector(".wb-ending-mode")) {
    const mode = document.createElement("div");
    mode.className = "wb-ending-mode";
    const defaultButton = document.createElement("button");
    defaultButton.type = "button";
    defaultButton.className = "wb-ending-choice";
    const defaultTitle = document.createElement("strong");
    defaultTitle.textContent = "世界の既定を使う";
    const defaultDescription = document.createElement("span");
    defaultDescription.textContent = "世界に定義済みの結末を固定";
    defaultButton.append(defaultTitle, defaultDescription);
    const customButton = document.createElement("button");
    customButton.type = "button";
    customButton.className = "wb-ending-choice";
    const customTitle = document.createElement("strong");
    customTitle.textContent = "別の結末を指定";
    const customDescription = document.createElement("span");
    customDescription.textContent = "有効な結末IDを直接入力";
    customButton.append(customTitle, customDescription);
    mode.append(defaultButton, customButton);

    const summary = document.createElement("p");
    summary.className = "wb-ending-default-summary";
    summary.textContent = "既定の結末を読み込み中…";
    const note = document.createElement("p");
    note.className = "wb-ending-custom-note";
    note.textContent = "別の結末を複数指定するときはカンマで区切ります。";
    endingField.insertAdjacentElement("beforebegin", mode);
    endingField.insertAdjacentElement("beforebegin", summary);
    endingField.insertAdjacentElement("afterend", note);

    const setEndingMode = (custom, focus = false) => {
      defaultButton.setAttribute("aria-pressed", String(!custom));
      customButton.setAttribute("aria-pressed", String(custom));
      endingField.classList.toggle("wb-hidden-ending-field", !custom);
      note.hidden = !custom;
      if (!custom) {
        endingField.value = "";
        endingField.dispatchEvent(new Event("input", { bubbles: true }));
      } else if (focus) {
        endingField.focus();
      }
    };
    defaultButton.addEventListener("click", () => setEndingMode(false));
    customButton.addEventListener("click", () => setEndingMode(true, true));
    endingField.addEventListener("input", () => {
      if (endingField.value.trim()) setEndingMode(true);
    });
    setEndingMode(Boolean(endingField.value.trim()));

    const projectField = document.querySelector('[name="project_id"]');
    const loadWorldSummary = async () => {
      const project = projectField?.value || inferredWorld;
      if (!project) {
        summary.textContent = "世界を選ぶと既定の結末を表示します。";
        return;
      }
      try {
        const response = await fetch("/worlds/" + encodeURIComponent(project));
        const doc = new DOMParser().parseFromString(await response.text(), "text/html");
        const paragraphs = [...doc.querySelectorAll("main p")].map((p) => p.textContent.trim()).filter(Boolean);
        const description = paragraphs.find((text) => text.includes("物語（") || text.includes("立ちはだかる"));
        summary.textContent = description ? "既定: " + description : "この世界に定義済みの結末を使います。";
      } catch (_) {
        summary.textContent = "この世界に定義済みの結末を使います。";
      }
    };
    projectField?.addEventListener("change", loadWorldSummary);
    loadWorldSummary();
  }
});


// WB-UI-027 follow-up: keep ending choice state and field visibility in sync.
document.addEventListener("click", (event) => {
  const choice = event.target.closest?.(".wb-ending-choice");
  if (!choice) return;

  const field = choice.closest(".field");
  const input = field?.querySelector('[name="evolution.target_ending"]');
  if (!field || !input) return;

  const choices = [...field.querySelectorAll(".wb-ending-choice")];
  const custom = choice.textContent.includes("別の結末を指定");
  choices.forEach((button) => {
    button.setAttribute("aria-pressed", String(button === choice));
  });

  input.classList.toggle("wb-hidden-ending-field", !custom);
  const note = field.querySelector(".wb-ending-custom-note");
  const summary = field.querySelector(".wb-ending-default-summary");
  if (note) note.hidden = !custom;
  if (summary) summary.hidden = custom;

  if (custom) {
    input.focus();
  } else {
    input.value = "";
  }
});


// WB-UI-029: preserve existing save controls while putting reading first.
document.addEventListener("DOMContentLoaded", () => {
  const workspace = document.querySelector(".world-editorial .world-workspace");
  if (!workspace) return;
  const panels = workspace.querySelector(".tab-panels");
  const people = workspace.querySelector(".world-people");
  const links = [...workspace.querySelectorAll("[data-world-person]")];
  const articles = [...workspace.querySelectorAll("[data-person-panel]")];
  const radios = [...workspace.querySelectorAll(".tab-input")];
  const detail = workspace.querySelector(".world-people-detail");
  const tabPanels = [...panels.children];
  const setTab = () => {
    const index = radios.findIndex((radio) => radio.checked);
    tabPanels.forEach((panel, i) => { panel.hidden = i !== index; });
    panels.classList.toggle("is-people", index === 1 && links.length > 0);
    panels.scrollTop = 0;
  };
  radios.forEach((radio) => radio.addEventListener("change", setTab));
  setTab();
  const selectPerson = (index, moveFocus = false) => {
    links.forEach((link) => link.setAttribute("aria-current", String(link.dataset.worldPerson === index)));
    articles.forEach((article) => { article.hidden = article.dataset.personPanel !== index; });
    if (detail) detail.scrollTop = 0;
    const selected = articles.find((article) => article.dataset.personPanel === index);
    if (moveFocus && selected) {
      selected.focus({preventScroll: true});
      if (matchMedia("(max-width: 1023px)").matches) selected.scrollIntoView({block: "start"});
    }
  };
  if (links.length) {
    people.classList.add("is-interactive");
    selectPerson(links[0].dataset.worldPerson);
    links.forEach((link) => link.addEventListener("click", (event) => {
      event.preventDefault();
      selectPerson(link.dataset.worldPerson, true);
    }));
  }
  workspace.querySelectorAll("[data-world-sheet]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.worldSheet)?.showModal());
  });

  const library = workspace.matches('[data-wb="library"]') ? workspace : null;
  const editorGroups = [...workspace.querySelectorAll(".editor-group")];
  if (!library) {
    workspace.querySelectorAll('a[href="#world-files"]').forEach((link) => {
      link.replaceWith(document.createTextNode("編集はStudioで利用できます"));
    });
    return;
  }
  const dialog = document.createElement("dialog");
  dialog.className = "world-editor-dialog";
  dialog.setAttribute("aria-labelledby", "world-editor-title");
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.id = "world-editor-title";
  title.textContent = "世界の内容を編集";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "button";
  close.textContent = "閉じる";
  close.addEventListener("click", () => dialog.close());
  header.append(title, close);
  dialog.append(header);
  const note = document.createElement("p");
  note.textContent = "設定ファイルを編集します。保存した内容を本文へ反映するには再読み込みしてください。閉じても入力は残ります。";
  dialog.append(note);
  const reload = document.createElement("button");
  reload.type = "button";
  reload.className = "button";
  reload.textContent = "保存した内容を再読み込み";
  dialog.append(reload);
  library.append(dialog);
  const openEditor = (group, textarea = null) => {
    editorGroups.forEach((item) => { item.hidden = item !== group; });
    group.open = true;
    if (textarea) {
      const editor = textarea.closest("details");
      if (editor) editor.open = true;
    }
    if (!dialog.open) dialog.showModal();
    if (textarea) { textarea.focus(); textarea.scrollIntoView({block: "center"}); }
  };
  editorGroups.forEach((group) => {
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "button world-edit-trigger";
    trigger.textContent = group.id === "subject-files" ? "人物の追加・設定ファイルを編集" : "世界の設定ファイルを編集";
    group.before(trigger);
    dialog.append(group);
    trigger.addEventListener("click", () => openEditor(group));
  });
  const worldGroup = editorGroups.find((group) => group.querySelector("#world-files"));
  workspace.querySelectorAll('a[href="#world-files"]').forEach((link) => {
    link.textContent = "この内容を編集";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (worldGroup) openEditor(worldGroup, worldGroup.querySelector('[data-file="world.yaml"]'));
    });
  });
  // Subject file order is not a stable identifier: match the saved YAML id.
  const subjectGroup = editorGroups.find((group) => group.id === "subject-files");
  const subjectAreas = [...dialog.querySelectorAll('textarea[data-file^="subjects/"]')];
  workspace.querySelectorAll("[data-world-edit-subject]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = subjectAreas.find((area) => area.dataset.subjectId === button.dataset.worldEditSubject);
      if (subjectGroup) openEditor(subjectGroup, target || null);
    });
  });
  const areas = [...dialog.querySelectorAll("textarea")];
  const saved = new Map(areas.map((area) => [area, area.value]));
  const dirty = () => areas.some((area) => area.value !== saved.get(area));
  areas.forEach((area) => area.addEventListener("input", () => {
    const status = area.closest(".editor")?.querySelector("[data-save-status]");
    if (status) status.textContent = area.value === saved.get(area) ? "変更なし" : "未保存の変更があります";
  }));
  dialog.querySelectorAll('[data-action="save-file"]').forEach((button) => {
    button.addEventListener("click", () => {
      const editor = button.closest(".editor");
      const area = editor?.querySelector("textarea");
      const status = editor?.querySelector("[data-save-status]");
      if (!area || !status) return;
      const submitted = area.value;
      status.textContent = "保存中…";
      const observer = new MutationObserver(() => {
        if (status.textContent.startsWith("保存しました")) {
          saved.set(area, submitted);
          observer.disconnect();
          if (area.value !== submitted) status.textContent += "（追加の変更は未保存）";
        } else if (!button.disabled) observer.disconnect();
      });
      observer.observe(status, {childList: true, subtree: true, characterData: true});
      const completion = new MutationObserver(() => {
        if (!button.disabled) {
          if (status.textContent === "保存中…") status.textContent = "保存できませんでした";
          completion.disconnect();
          observer.disconnect();
        }
      });
      completion.observe(button, {attributes: true, attributeFilter: ["disabled"]});
    });
  });
  reload.addEventListener("click", () => {
    if (!dirty() || window.confirm("未保存の変更があります。変更を破棄して再読み込みしますか？")) location.reload();
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty()) { event.preventDefault(); event.returnValue = ""; }
  });
});
