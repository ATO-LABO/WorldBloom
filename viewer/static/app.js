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

  // WB-UI-018: character table rows open the matching stat-sheet <dialog>.
  const openSheet = (row) => {
    const dialog = document.getElementById(row.dataset.sheet);
    if (dialog && typeof dialog.showModal === "function" && !dialog.open) {
      dialog.showModal();
    }
  };

  document.querySelectorAll("tr[data-sheet]").forEach((row) => {
    row.addEventListener("click", () => openSheet(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openSheet(row);
      }
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
})();
