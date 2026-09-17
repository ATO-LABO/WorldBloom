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

  // Home's "n 件" link points to /worlds/<id>#experiments; land on the
  // 実行履歴 tab (tab-world-5) instead of the default 概要 tab.
  if (location.hash === "#experiments") {
    const experimentsTab = document.getElementById("tab-world-5");
    if (experimentsTab) {
      experimentsTab.checked = true;
    }
  }

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
})();
