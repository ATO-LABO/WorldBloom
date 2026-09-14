"use strict";

(() => {
  const parseJsonAttr = (text, fallback) => {
    try {
      return JSON.parse(text || "");
    } catch (error) {
      return fallback;
    }
  };

  const api = async (method, path, body) => {
    const hasBody = method !== "GET" && method !== "HEAD";
    const init = { method, headers: { "X-WorldBloom-Client": "1" } };
    if (hasBody) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body === undefined ? {} : body);
    }
    const response = await fetch(path, init);
    let json = null;
    try {
      json = await response.json();
    } catch (error) {
      json = null;
    }
    return { status: response.status, json };
  };

  const clearErrors = (root) => {
    root.querySelectorAll("[data-error-for]").forEach((el) => {
      el.textContent = "";
    });
    const formError = root.querySelector("[data-form-error]");
    if (formError) {
      formError.textContent = "";
    }
  };

  const applyErrors = (root, payload) => {
    clearErrors(root);
    const fieldErrors = (payload && payload.field_errors) || {};
    const handled = new Set();
    Object.keys(fieldErrors).forEach((field) => {
      const target = root.querySelector(`[data-error-for="${CSS.escape(field)}"]`);
      if (target) {
        target.textContent = fieldErrors[field];
        handled.add(field);
      }
    });
    const formError = root.querySelector("[data-form-error]");
    if (formError) {
      const rest = Object.keys(fieldErrors)
        .filter((field) => !handled.has(field))
        .map((field) => fieldErrors[field]);
      const message = (payload && payload.message) || "";
      formError.textContent = [message, ...rest].filter(Boolean).join(" / ");
    }
  };

  const collectConfig = (form) => {
    const result = {};
    form.querySelectorAll("[data-field]").forEach((el) => {
      const path = el.dataset.field.split(".");
      let target = result;
      for (let i = 0; i < path.length - 1; i += 1) {
        target[path[i]] = target[path[i]] || {};
        target = target[path[i]];
      }
      const key = path[path.length - 1];
      let value;
      if (el.type === "checkbox") {
        value = el.checked;
      } else if (el.type === "number") {
        value = el.value === "" ? null : Number(el.value);
      } else if (el.dataset.field === "evolution.target_ending") {
        const parts = el.value.split(",").map((part) => part.trim()).filter(Boolean);
        value = parts.length ? parts : null;
      } else if (el.dataset.field === "generation.model") {
        value = el.value === "" ? null : el.value;
      } else {
        value = el.value;
      }
      target[key] = value;
    });
    return result;
  };

  const renderPreview = (container, preview) => {
    container.textContent = "";
    if (!preview) {
      return;
    }
    const rows = [
      ["世界", preview.world_name],
      ["主人公", preview.protagonist],
      ["敵役", preview.antagonist],
      ["解決済み結末", (preview.target_endings || []).join(", ")],
      ["最大ターン", preview.max_turns],
      ["個体評価数（予定）", preview.planned_individual_evaluations],
      ["seed評価数（予定）", preview.planned_seed_evaluations],
      [
        "seed範囲",
        preview.seed_range ? `${preview.seed_range.first} から ${preview.seed_range.count} 件` : "",
      ],
      [
        "省略ファイル",
        preview.fallbacks && Object.keys(preview.fallbacks).length
          ? Object.keys(preview.fallbacks).join(", ")
          : "省略なし",
      ],
      [
        "生成可否",
        preview.generation
          ? `${preview.generation.available} (${preview.generation.authentication})`
          : "",
      ],
    ];
    const dl = document.createElement("dl");
    rows.forEach(([label, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value === undefined || value === null ? "" : String(value);
      dl.append(dt, dd);
    });
    container.append(dl);
  };

  const initConfigForm = () => {
    const form = document.querySelector('[data-wb="config-form"]');
    if (!form) {
      return;
    }
    const previewButton = form.querySelector('[data-action="preview"]');
    const previewContainer = form.querySelector("[data-preview]");
    if (previewButton && previewContainer) {
      previewButton.addEventListener("click", async () => {
        previewButton.disabled = true;
        try {
          const { status, json } = await api("POST", "/api/configs/preview", collectConfig(form));
          if (status === 200) {
            clearErrors(form);
            renderPreview(previewContainer, json.preview);
          } else {
            applyErrors(form, json);
          }
        } catch (error) {
          applyErrors(form, { message: "サーバーに接続できません" });
        } finally {
          previewButton.disabled = false;
        }
      });
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const parent = form.dataset.parent;
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
      }
      try {
        const { status, json } = parent
          ? await api("POST", `/api/configs/${encodeURIComponent(parent)}/duplicate`, {
              changes: collectConfig(form),
            })
          : await api("POST", "/api/configs", collectConfig(form));
        if (status === 201 && json && json.config_id) {
          window.location.href = `/configs/${encodeURIComponent(json.config_id)}`;
          return;
        }
        applyErrors(form, json);
      } catch (error) {
        applyErrors(form, { message: "サーバーに接続できません" });
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
        }
      }
    });
  };

  const initStart = () => {
    const root = document.querySelector('[data-wb="start"]');
    if (!root) {
      return;
    }
    const form = root.querySelector("form");
    if (!form) {
      return;
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      const originalLabel = button ? button.textContent : "";
      if (button) {
        button.disabled = true;
        button.textContent = "起動しています…";
      }
      const errorArea = root.querySelector("[data-form-error]");
      if (errorArea) {
        errorArea.textContent = "";
      }
      let status;
      let json;
      try {
        ({ status, json } = await api("POST", "/api/jobs", {
          request_id: root.dataset.requestId,
          kind: "evolve",
          config_id: root.dataset.configId,
        }));
      } catch (error) {
        if (button) {
          button.disabled = false;
          button.textContent = originalLabel;
        }
        if (errorArea) {
          errorArea.textContent = "サーバーに接続できません";
        }
        return;
      }
      if ((status === 202 || status === 200) && json && json.job_id) {
        window.location.href = `/jobs/${encodeURIComponent(json.job_id)}`;
        return;
      }
      if (button) {
        button.disabled = false;
        button.textContent = originalLabel;
      }
      if (errorArea) {
        if (status === 409) {
          errorArea.textContent = "他の処理が実行中または状態確認中です。実行履歴を確認してください。";
        } else {
          errorArea.textContent = (json && json.message) || `HTTP ${status}`;
        }
      }
    });
  };

  const setText = (root, field, value) => {
    const el = root.querySelector(`[data-field="${field}"]`);
    if (el) {
      el.textContent = value === undefined || value === null ? "" : String(value);
    }
  };

  // §3.6: elapsed seconds falls back to started_at (or created_at) through
  // finished_at (or "now") whenever the GA hasn't reported elapsed_seconds
  // itself yet (e.g. still queued/preparing).
  const elapsedSeconds = (job) => {
    const progress = job.progress || {};
    if (progress.elapsed_seconds !== undefined && progress.elapsed_seconds !== null) {
      return progress.elapsed_seconds;
    }
    const start = job.started_at || job.created_at;
    if (!start) {
      return null;
    }
    const end = job.finished_at || Date.now() / 1000;
    return Math.round((end - start) * 10) / 10;
  };

  const applyJob = (root, job, labels) => {
    const stateLabels = labels.state;
    const phaseLabels = labels.phase;

    const stateEl = root.querySelector('[data-field="state"]');
    if (stateEl) {
      stateEl.className = `state-badge state-${job.state}`;
      const labelEl = stateEl.querySelector('[data-field="state-label"]');
      if (labelEl) {
        labelEl.textContent = stateLabels[job.state] || job.state;
      }
    }
    setText(root, "phase", phaseLabels[job.phase] || job.phase || "—");

    const reconciliationEl = root.querySelector('[data-field="reconciliation"]');
    if (reconciliationEl) {
      reconciliationEl.hidden = job.reconciliation !== "unknown";
    }

    setText(root, "completed_generations", (job.progress || {}).completed_generations);
    setText(root, "total_generations", (job.progress || {}).total_generations);
    setText(root, "completed_individuals", (job.progress || {}).completed_individuals);
    setText(root, "total_individuals", (job.progress || {}).total_individuals);
    setText(root, "completed_seeds", (job.progress || {}).completed_seeds);
    setText(root, "total_seeds", (job.progress || {}).total_seeds);
    const activeSeeds = (job.progress || {}).active_seeds;
    if (activeSeeds) {
      setText(root, "active_seeds", activeSeeds.length);
    }
    setText(root, "elapsed_seconds", elapsedSeconds(job));
    setText(
      root,
      "publication_revision",
      job.publication_revision === undefined || job.publication_revision === null
        ? "—"
        : job.publication_revision
    );

    // Generation jobs (synopsize/narrate) carry progress.completed/total and a
    // top-level counts map instead of the GA fields above (§3.2, WB-UI-008).
    setText(root, "completed", (job.progress || {}).completed);
    setText(root, "total", (job.progress || {}).total);
    const counts = job.counts || (job.progress || {}).counts;
    if (counts) {
      const countsEl = root.querySelector('[data-field="counts"]');
      if (countsEl) {
        // data-entry-labels carries ENTRY_STATUS_LABELS (server-rendered);
        // an unmapped key falls back to itself.
        const entryLabels = parseJsonAttr(root.dataset.entryLabels, {});
        countsEl.textContent = Object.keys(counts)
          .sort()
          .map((key) => `${entryLabels[key] || key} ${counts[key]}`)
          .join(" · ");
      }
    }
  };

  const initJob = () => {
    const root = document.querySelector('[data-wb="job"]');
    if (!root) {
      return;
    }
    const jobId = root.dataset.jobId;
    // Parse the server-rendered vocabulary once; the attributes are constants.
    const terminalStates = new Set(parseJsonAttr(root.dataset.terminalStates, []));
    const labels = {
      state: parseJsonAttr(root.dataset.stateLabels, {}),
      phase: parseJsonAttr(root.dataset.phaseLabels, {}),
    };
    let timer = null;
    let stopped = root.dataset.terminal === "true";
    const connectionNote = root.querySelector("[data-connection-status]");

    const cancelButton = root.querySelector('[data-action="cancel"]');
    const cancelStatus = root.querySelector("[data-cancel-status]");
    if (cancelButton) {
      cancelButton.addEventListener("click", async () => {
        if (!window.confirm("実行を停止しますか？")) {
          return;
        }
        cancelButton.disabled = true;
        if (cancelStatus) {
          cancelStatus.textContent = "";
        }
        try {
          const { status, json } = await api("POST", `/api/jobs/${encodeURIComponent(jobId)}/cancel`, {});
          if (status === 202 || status === 200) {
            if (cancelStatus) {
              cancelStatus.textContent = "停止を要求しました";
            }
            window.clearTimeout(timer);
            poll();
          } else {
            cancelButton.disabled = false;
            if (cancelStatus) {
              cancelStatus.textContent = (json && json.message) || `停止できませんでした（HTTP ${status}）`;
            }
          }
        } catch (error) {
          cancelButton.disabled = false;
          if (cancelStatus) {
            cancelStatus.textContent = "サーバーに接続できません";
          }
        }
      });
    }

    const poll = async () => {
      if (stopped) {
        return;
      }
      try {
        const { status, json } = await api("GET", `/api/jobs/${encodeURIComponent(jobId)}`);
        if (status === 200 && json) {
          if (connectionNote) {
            connectionNote.hidden = true;
          }
          applyJob(root, json, labels);
          if (terminalStates.has(json.state)) {
            stopped = true;
            window.location.reload();
            return;
          }
        }
      } catch (error) {
        if (connectionNote) {
          connectionNote.hidden = false;
        }
      }
      if (!stopped) {
        timer = window.setTimeout(poll, 2000);
      }
    };

    if (!stopped) {
      timer = window.setTimeout(poll, 2000);
    }
  };

  // WB-UI-008: the confirmation page's [この内容で生成を開始] button. The
  // request body is the server-rendered data-request JSON, sent verbatim.
  const initGenerate = () => {
    const root = document.querySelector('[data-wb="generate"]');
    if (!root) {
      return;
    }
    const form = root.querySelector("form");
    if (!form || !root.dataset.request) {
      return;
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const ackBox = form.querySelector("[data-ack]");
      const errorArea = form.querySelector("[data-form-error]");
      if (ackBox && !ackBox.checked) {
        if (errorArea) {
          errorArea.textContent = "二重生成の可能性を確認するチェックが必要です";
        }
        return;
      }
      const button = form.querySelector('button[type="submit"]');
      if (button) {
        button.disabled = true;
      }
      let status;
      let json;
      try {
        ({ status, json } = await api("POST", "/api/jobs", JSON.parse(root.dataset.request)));
      } catch (error) {
        if (button) {
          button.disabled = false;
        }
        if (errorArea) {
          errorArea.textContent = "サーバーに接続できません";
        }
        return;
      }
      if ((status === 202 || status === 200) && json && json.job_id) {
        window.location.href = `/jobs/${encodeURIComponent(json.job_id)}`;
        return;
      }
      if (button) {
        button.disabled = false;
      }
      applyErrors(form, json);
    });
  };

  // WB-UI-008: output detail page. Polls /api/outputs/{id} while generation is
  // running, fills in completed entries' text without a reload, and wires the
  // [復旧] button (POST /api/outputs/{id}/recover).
  const initOutput = () => {
    const root = document.querySelector('[data-wb="output"]');
    if (!root) {
      return;
    }
    const outputId = root.dataset.outputId;
    const terminalStates = new Set(parseJsonAttr(root.dataset.terminalStates, []));
    const statusLabels = parseJsonAttr(root.dataset.statusLabels, {});
    let stopped = root.dataset.terminal === "true";
    const connectionNote = root.querySelector("[data-connection-status]");

    const recoverButton = root.querySelector('[data-action="recover"]');
    if (recoverButton) {
      recoverButton.addEventListener("click", async () => {
        recoverButton.disabled = true;
        const statusEl = root.querySelector("[data-recover-status]");
        try {
          const { status, json } = await api("POST", `/api/outputs/${encodeURIComponent(outputId)}/recover`, {});
          if (status === 200) {
            window.location.reload();
            return;
          }
          if (statusEl) {
            statusEl.textContent = (json && json.message) || `HTTP ${status}`;
          }
        } catch (error) {
          if (statusEl) {
            statusEl.textContent = "サーバーに接続できません";
          }
        }
        recoverButton.disabled = false;
      });
    }

    const fillText = async (article, entry) => {
      if (article.querySelector('[data-field="text"]')) {
        return;
      }
      try {
        const response = await fetch(
          `/outputs/${encodeURIComponent(outputId)}/entries/${encodeURIComponent(entry.candidate_id)}/text`,
          { headers: { "X-WorldBloom-Client": "1" } }
        );
        if (!response.ok) {
          return;
        }
        const text = await response.text();
        const container = document.createElement("div");
        container.className = "story-text";
        container.dataset.field = "text";
        text.split("\n\n").forEach((paragraph) => {
          if (!paragraph.trim()) {
            return;
          }
          const p = document.createElement("p");
          p.textContent = paragraph;
          container.append(p);
        });
        article.append(container);
      } catch (error) {
        // Left for the next poll or a manual reload.
      }
    };

    const applyEntry = (article, entry) => {
      article.dataset.status = entry.status;
      const badge = article.querySelector('[data-field="state"]');
      if (badge) {
        badge.className = `state-badge state-${entry.status}`;
        const label = badge.querySelector('[data-field="state-label"]');
        if (label) {
          label.textContent = statusLabels[entry.status] || entry.status;
        }
      }
      setText(article, "message", entry.message);
      if (entry.status === "ok") {
        fillText(article, entry);
      }
    };

    const poll = async () => {
      if (stopped) {
        return;
      }
      try {
        const { status, json } = await api("GET", `/api/outputs/${encodeURIComponent(outputId)}`);
        if (status === 200 && json) {
          if (connectionNote) {
            connectionNote.hidden = true;
          }
          (json.entries || []).forEach((entry) => {
            const article = root.querySelector(`[data-entry="${CSS.escape(entry.candidate_id)}"]`);
            if (article) {
              applyEntry(article, entry);
            }
          });
          // A null job_state (no owning job record) is terminal too: nothing
          // to reconcile, so stop polling exactly like a terminal state.
          if (json.job_state == null || terminalStates.has(json.job_state)) {
            stopped = true;
            window.location.reload();
            return;
          }
        }
      } catch (error) {
        if (connectionNote) {
          connectionNote.hidden = false;
        }
      }
      if (!stopped) {
        window.setTimeout(poll, 2000);
      }
    };

    if (!stopped) {
      window.setTimeout(poll, 2000);
    }
  };

  const initCandidates = () => {
    const root = document.querySelector('[data-wb="candidates"]');
    if (!root) {
      return;
    }
    const runId = root.dataset.runId;
    root.querySelectorAll("tr[data-candidate-id]").forEach((row) => {
      const button = row.querySelector('[data-action="save-candidate"]');
      if (!button) {
        return;
      }
      button.addEventListener("click", async () => {
        const statusEl = row.querySelector("[data-save-status]");
        const stateSelect = row.querySelector('[data-field="state"]');
        const noteInput = row.querySelector('[data-field="note"]');
        button.disabled = true;
        try {
          const { status, json } = await api("POST", `/api/runs/${encodeURIComponent(runId)}/selection`, {
            expected_revision: Number(root.dataset.revision),
            changes: [{ candidate_id: row.dataset.candidateId, state: stateSelect.value, note: noteInput.value }],
          });
          if (status === 200 && json) {
            root.dataset.revision = String(json.revision);
            if (statusEl) {
              statusEl.textContent = `保存しました（版 ${json.revision}）`;
            }
          } else if (status === 409) {
            if (statusEl) {
              statusEl.textContent = `他のタブで選定が更新されています（現在版 ${
                json ? json.current_revision : "?"
              }）。再読み込みしてください`;
            }
          } else if (statusEl) {
            statusEl.textContent = (json && json.message) || `HTTP ${status}`;
          }
        } catch (error) {
          if (statusEl) {
            statusEl.textContent = "サーバーに接続できません";
          }
        } finally {
          button.disabled = false;
        }
      });
    });
  };

  const initTray = () => {
    const root = document.querySelector('[data-wb="tray"]');
    if (!root) {
      return;
    }
    root.querySelectorAll("tr[data-candidate-id]").forEach((row) => {
      const button = row.querySelector('[data-action="remove"]');
      if (!button) {
        return;
      }
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const { status } = await api("POST", `/api/runs/${encodeURIComponent(row.dataset.runId)}/selection`, {
            expected_revision: Number(row.dataset.revision),
            changes: [{ candidate_id: row.dataset.candidateId, state: "unclassified" }],
          });
          if (status === 200) {
            window.location.reload();
          } else {
            button.disabled = false;
          }
        } catch (error) {
          button.disabled = false;
        }
      });
    });
  };

  initConfigForm();
  initStart();
  initJob();
  initGenerate();
  initOutput();
  initCandidates();
  initTray();
})();
