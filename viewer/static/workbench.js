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
      if (el.type === "radio") {
        if (!el.checked) {
          return;
        }
        value = el.value;
      } else if (el.type === "checkbox") {
        value = el.checked;
      } else if (el.type === "number") {
        value = el.value === "" ? null : Number(el.value);
      } else if (el.dataset.field === "evolution.target_ending") {
        const parts = el.value.split(",").map((part) => part.trim()).filter(Boolean);
        value = parts.length ? parts : null;
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

    // WB-UI-019: picking a world with a known genre snaps the genre select to
    // match, instead of leaving a mismatched pair silently in place.
    const worldSelect = form.querySelector('select[data-field="project_id"]');
    const genreSelect = form.querySelector('select[data-field="template_id"]');
    if (worldSelect && genreSelect) {
      worldSelect.addEventListener("change", () => {
        const genre = worldSelect.selectedOptions[0] && worldSelect.selectedOptions[0].dataset.genre;
        if (genre && Array.from(genreSelect.options).some((opt) => opt.value === genre)) {
          genreSelect.value = genre;
        }
      });
    }

    // WB-UI-019: the "探索の規模" section's live per-generation/total run
    // count and the sticky footer's one-line summary.
    const escapeHtml = (text) =>
      String(text).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
      }[ch]));
    const updateSummary = () => {
      const num = (name) => {
        const el = form.querySelector(`[data-field="${name}"]`);
        const n = el ? Number(el.value) : NaN;
        return Number.isFinite(n) ? n : 0;
      };
      const generations = num("evolution.generations");
      const population = num("evolution.population");
      const seeds = num("evolution.seeds");
      const perGenEl = form.querySelector("[data-per-gen]");
      if (perGenEl) {
        perGenEl.textContent = (population * seeds).toLocaleString("ja-JP");
      }
      const totalEl = form.querySelector("[data-total]");
      if (totalEl) {
        totalEl.textContent = (generations * population * seeds).toLocaleString("ja-JP");
      }
      const summaryEl = form.querySelector("[data-summary]");
      if (summaryEl) {
        const worldField = form.querySelector('[name="project_id"]');
        const genreField = form.querySelector('[name="template_id"]');
        const world = worldField ? worldField.value : "";
        const genre = genreField ? genreField.value : "";
        summaryEl.innerHTML =
          `<b>${escapeHtml(world)}</b> × ${escapeHtml(genre)} ・ ` +
          `${generations} 世代 × ${population} 個体 × ${seeds} seed`;
      }
    };
    ["evolution.generations", "evolution.population", "evolution.seeds"].forEach((name) => {
      const el = form.querySelector(`[data-field="${name}"]`);
      if (el) {
        el.addEventListener("input", updateSummary);
        el.addEventListener("change", updateSummary);
      }
    });
    if (worldSelect) {
      worldSelect.addEventListener("input", updateSummary);
      worldSelect.addEventListener("change", updateSummary);
    }
    if (genreSelect) {
      genreSelect.addEventListener("input", updateSummary);
      genreSelect.addEventListener("change", updateSummary);
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

  // WB-UI-021: /configs's 文章生成 card. Picking a backend snaps model/limits
  // to that backend's own settings.json values (data-backends), same idea as
  // initConfigForm's world->genre snap above.
  const initOutputSettings = () => {
    const form = document.querySelector('[data-wb="output-settings"]');
    if (!form) {
      return;
    }
    let backends = parseJsonAttr(form.dataset.backends, {});
    const apiKeyBackends = new Set(parseJsonAttr(form.dataset.apiKeyBackends, []));
    const datalist = document.getElementById("output-model-list");
    const modelHint = form.querySelector("[data-model-hint]");
    const apiKeyField = form.querySelector("[data-api-key-field]");
    const apiKeyInput = form.querySelector("[data-apikey]");
    const apiKeyStatus = form.querySelector("[data-api-key-status]");
    const updateDatalist = (models) => {
      if (!datalist) {
        return;
      }
      datalist.textContent = "";
      (models || []).forEach((model) => {
        const option = document.createElement("option");
        option.value = model;
        datalist.appendChild(option);
      });
    };
    // Ollama/llama-server/anthropic/openai expose a real "list models" API -- ask it for
    // this backend's actual catalog so the model field's dropdown offers only
    // models that really exist, instead of whatever the user remembers to
    // type. codex-cli/claude-cli have no such API (live: false); their
    // dropdown stays limited to models 疎通テスト has already confirmed.
    const fetchModels = async (backend) => {
      try {
        const { status, json } = await api("GET", `/api/settings/output/models?backend=${encodeURIComponent(backend)}`);
        if (status !== 200 || !json) {
          return;
        }
        updateDatalist(json.models);
        if (modelHint) {
          if (!json.live) {
            modelHint.textContent = "この方式は候補の自動取得に対応していません。モデル名を入力し、疎通テストで確認してください。";
          } else {
            modelHint.textContent = json.reason_label || "";
          }
        }
      } catch (error) {
        // best-effort: the datalist keeps its server-rendered verified_models.
      }
    };
    const applyBackend = (backend) => {
      const info = backends[backend] || {};
      const modelInput = form.querySelector('[data-field="model"]');
      if (modelInput) {
        modelInput.value = info.model || "";
      }
      Object.entries(info.limits || {}).forEach(([key, value]) => {
        const el = form.querySelector(`[data-field="limits.${key}"]`);
        if (el) {
          el.value = value;
        }
      });
      updateDatalist(info.verified_models);
      fetchModels(backend);
      if (apiKeyField) {
        apiKeyField.hidden = !apiKeyBackends.has(backend);
      }
      if (apiKeyInput) {
        apiKeyInput.value = "";
      }
      if (apiKeyStatus) {
        apiKeyStatus.textContent = info.has_api_key ? "設定済み(変更する場合のみ入力)" : "未設定";
      }
    };
    const backendSelect = form.querySelector('select[name="backend"]');
    if (backendSelect) {
      backendSelect.addEventListener("change", () => applyBackend(backendSelect.value));
      fetchModels(backendSelect.value);
    }
    const saveApiKeyButton = form.querySelector("[data-wb-save-api-key]");
    if (saveApiKeyButton) {
      saveApiKeyButton.addEventListener("click", async () => {
        if (!backendSelect || !apiKeyInput) {
          return;
        }
        const backend = backendSelect.value;
        saveApiKeyButton.disabled = true;
        if (apiKeyStatus) {
          apiKeyStatus.textContent = "保存中…";
        }
        try {
          const { status, json } = await api("POST", "/api/settings/output/api-key", {
            backend, api_key: apiKeyInput.value,
          });
          if (status === 200 && json) {
            apiKeyInput.value = "";
            if (json.backends) {
              backends = json.backends;
              form.dataset.backends = JSON.stringify(json.backends);
            }
            if (apiKeyStatus) {
              apiKeyStatus.textContent = "設定済み(変更する場合のみ入力)";
            }
            fetchModels(backend);
          } else if (apiKeyStatus) {
            apiKeyStatus.textContent = (json && json.message) || "保存に失敗しました";
          }
        } catch (error) {
          if (apiKeyStatus) {
            apiKeyStatus.textContent = "サーバーに接続できません";
          }
        } finally {
          saveApiKeyButton.disabled = false;
        }
      });
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
      }
      try {
        const payload = collectConfig(form);
        if (payload.model === "") {
          payload.model = null;
        }
        const { status, json } = await api("POST", "/api/settings/output", payload);
        if (status === 200 && json) {
          clearErrors(form);
          const availEl = form.querySelector("[data-availability]");
          if (availEl && json.availability) {
            availEl.textContent = json.availability.label || "";
          }
          if (json.backends) {
            backends = json.backends;
            form.dataset.backends = JSON.stringify(json.backends);
          }
        } else {
          applyErrors(form, json);
        }
      } catch (error) {
        applyErrors(form, { message: "サーバーに接続できません" });
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
        }
      }
    });
    const testButton = form.querySelector("[data-wb-test-model]");
    const availEl = form.querySelector("[data-availability]");
    if (testButton) {
      testButton.addEventListener("click", async () => {
        if (!backendSelect) {
          return;
        }
        const modelInput = form.querySelector('[data-field="model"]');
        testButton.disabled = true;
        if (availEl) {
          availEl.textContent = "確認中…";
        }
        try {
          const { status, json } = await api("POST", "/api/settings/output/test", {
            backend: backendSelect.value,
            model: modelInput && modelInput.value ? modelInput.value : null,
          });
          if (status === 200 && json) {
            if (availEl) {
              availEl.textContent = (json.availability && json.availability.label) || "";
            }
            if (json.backends) {
              backends = json.backends;
              form.dataset.backends = JSON.stringify(json.backends);
              updateDatalist((json.backends[backendSelect.value] || {}).verified_models);
            }
          } else if (availEl) {
            availEl.textContent = (json && json.message) || "確認に失敗しました";
          }
        } catch (error) {
          if (availEl) {
            availEl.textContent = "サーバーに接続できません";
          }
        } finally {
          testButton.disabled = false;
        }
      });
    }
  };

  // WB-UI-017: the run page's idle-state config picker (a <select> inside a
  // GET form). With JS, switching the selection submits the form right away
  // instead of waiting for the <noscript> fallback button (browsers never
  // parse a <noscript> element's content into the DOM while scripting is
  // enabled, so there is nothing to hide here).
  const initRunConfigPicker = () => {
    document.querySelectorAll('[data-wb="run-config"] select').forEach((select) => {
      select.addEventListener("change", () => select.form.submit());
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

  // WB-UI-015: per-root "did this field's text change since the last poll"
  // tracking, so applyJob can flash only the fields that actually moved.
  const previousFieldText = new WeakMap();

  const setField = (root, field, value) => {
    const el = root.querySelector(`[data-field="${field}"]`);
    if (!el) {
      return;
    }
    const text = value === undefined || value === null ? "" : String(value);
    let store = previousFieldText.get(root);
    if (!store) {
      store = new Map();
      previousFieldText.set(root, store);
    }
    const prev = store.get(field);
    el.textContent = text;
    if (prev !== undefined && prev !== text) {
      el.classList.remove("changed");
      void el.offsetWidth; // restart the animation even if it just played
      el.classList.add("changed");
    }
    store.set(field, text);
  };

  // Removes the flash once its one-shot CSS animation (wb-flash, in app.css)
  // finishes, instead of a timer that could race a fast re-trigger.
  document.addEventListener("animationend", (event) => {
    if (event.animationName === "wb-flash") {
      event.target.classList.remove("changed");
    }
  });

  // WB-UI-015: pure by design (no DOM, no closure state) so it can be
  // exercised standalone via `node -e`. `kind` selects which fields of
  // prev/next to diff: "ga" reads completed_generations/completed_individuals
  // from progress-shaped objects, "generation" reads every key of a
  // counts-shaped object. `labels` (optional) maps a field/status key to its
  // display label, same fallback rule as applyJob's entryLabels lookup
  // elsewhere in this file (label || key).
  const DELTA_GA_FIELDS = [
    ["completed_generations", "世代"],
    ["completed_individuals", "評価済み"],
  ];

  const deltaText = (prev, next, kind, labels) => {
    if (!prev || !next) {
      return "";
    }
    labels = labels || {};
    const parts = [];
    if (kind === "ga") {
      DELTA_GA_FIELDS.forEach(([field, fallbackLabel]) => {
        const before = prev[field];
        const after = next[field];
        if (typeof before !== "number" || typeof after !== "number") {
          return;
        }
        const diff = after - before;
        if (diff !== 0) {
          parts.push(`${labels[field] || fallbackLabel} ${diff > 0 ? "+" : ""}${diff}`);
        }
      });
    } else if (kind === "generation") {
      const keys = new Set([...Object.keys(prev), ...Object.keys(next)]);
      Array.from(keys)
        .sort()
        .forEach((key) => {
          const diff = (next[key] || 0) - (prev[key] || 0);
          if (diff !== 0) {
            parts.push(`${labels[key] || key} ${diff > 0 ? "+" : ""}${diff}`);
          }
        });
    }
    return parts.join(" · ");
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

  const etaText = (progress, elapsed, state, terminalStates) => {
    if (terminalStates.has(state)) {
      return "—";
    }
    progress = progress || {};
    let completed = progress.completed_seeds;
    let total = progress.total_seeds;
    if (completed == null || total == null) {
      completed = progress.completed_individuals;
      total = progress.total_individuals;
    }
    if (completed == null || total == null) {
      // Generation jobs (synopsize/narrate) use progress.completed/total.
      completed = progress.completed;
      total = progress.total;
    }
    if (
      typeof completed !== "number" || typeof total !== "number" || typeof elapsed !== "number" ||
      completed <= 0 || total <= completed
    ) {
      return "—";
    }
    const remainingSeconds = (elapsed * (total - completed)) / completed;
    if (remainingSeconds < 60) {
      return "残り1分未満";
    }
    const minutes = Math.round(remainingSeconds / 60);
    const eta = new Date(Date.now() + remainingSeconds * 1000);
    const hh = String(eta.getHours()).padStart(2, "0");
    const mm = String(eta.getMinutes()).padStart(2, "0");
    return `残り約${minutes}分（${hh}:${mm}頃）`;
  };

  // WB-UI-015: per-root previous progress/counts snapshot, so applyJob can
  // hand deltaText() a (prev, next) pair on every poll.
  const jobSnapshots = new WeakMap();

  const applyJob = (root, job, labels) => {
    const terminalStates = new Set(parseJsonAttr(root.dataset.terminalStates, []));
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
    setField(root, "phase", phaseLabels[job.phase] || job.phase || "—");

    const reconciliationEl = root.querySelector('[data-field="reconciliation"]');
    if (reconciliationEl) {
      reconciliationEl.hidden = job.reconciliation !== "unknown";
    }

    const progress = job.progress || {};
    setField(root, "completed_generations", progress.completed_generations);
    setField(root, "total_generations", progress.total_generations);
    setField(root, "completed_individuals", progress.completed_individuals);
    setField(root, "total_individuals", progress.total_individuals);
    setField(root, "completed_seeds", progress.completed_seeds);
    setField(root, "total_seeds", progress.total_seeds);
    const activeSeeds = progress.active_seeds;
    if (activeSeeds) {
      setField(root, "active_seeds", activeSeeds.length);
    }
    const elapsed = elapsedSeconds(job);
    setField(root, "elapsed_seconds", elapsed);
    setField(
      root,
      "publication_revision",
      job.publication_revision === undefined || job.publication_revision === null
        ? "—"
        : job.publication_revision
    );
    setField(root, "eta", etaText(progress, elapsed, job.state, terminalStates));

    // §7: keep each rendered <progress> bar's value/max in step with polling
    // (the server only sets them on the initial render).
    const genProgress = root.querySelector('progress[aria-label="完了世代"]');
    if (genProgress && progress.total_generations) {
      genProgress.value = progress.completed_generations || 0;
      genProgress.max = progress.total_generations;
    }
    const individualProgress = root.querySelector('progress[aria-label="評価済み個体"]');
    if (individualProgress) {
      individualProgress.value = progress.completed_individuals || 0;
      individualProgress.max = progress.total_individuals || 1;
    }

    const seedProgress = root.querySelector('progress[aria-label="評価済みseed"]');
    if (seedProgress) {
      seedProgress.value = progress.completed_seeds || 0;
      seedProgress.max = progress.total_seeds || 1;
    }
    if (progress.metrics) {
      setText(root, "metric_occupied", progress.metrics.occupied_cells);
      if (typeof progress.metrics.average_archive_quality === "number") {
        setText(root, "metric_quality", progress.metrics.average_archive_quality.toFixed(2));
      }
      if (typeof progress.metrics.reach_rate === "number") {
        setText(root, "metric_reach", Math.round(progress.metrics.reach_rate * 100));
      }
    }

    // Generation jobs (synopsize/narrate) carry progress.completed/total and a
    // top-level counts map instead of the GA fields above (§3.2, WB-UI-008).
    setField(root, "completed", progress.completed);
    setField(root, "total", progress.total);
    // data-entry-labels carries ENTRY_STATUS_LABELS (server-rendered); an
    // unmapped key falls back to itself, both here and in deltaText() below.
    const entryLabels = parseJsonAttr(root.dataset.entryLabels, {});
    const counts = job.counts || (job.progress || {}).counts;
    if (counts) {
      const countsEl = root.querySelector('[data-field="counts"]');
      if (countsEl) {
        countsEl.textContent = Object.keys(counts)
          .sort()
          .map((key) => `${entryLabels[key] || key} ${counts[key]}`)
          .join(" · ");
      }
    }

    // WB-UI-015: last-updated time (client clock, so it means "as of this
    // poll" even across a stalled/reconnecting server) and a one-line summary
    // of what moved since the previous poll.
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    setText(root, "updated-at", `最終更新 ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`);
    const previous = jobSnapshots.get(root);
    if (counts) {
      setText(root, "delta", deltaText(previous, counts, "generation", entryLabels));
      jobSnapshots.set(root, { ...counts });
    } else {
      const gaSnapshot = {
        completed_generations: progress.completed_generations,
        completed_individuals: progress.completed_individuals,
      };
      setText(root, "delta", deltaText(previous, gaSnapshot, "ga"));
      jobSnapshots.set(root, gaSnapshot);
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
    // WB-UI-015: once a poll fails, remember when, so the next success can
    // show "復帰（N秒ぶり）" exactly once instead of the usual delta.
    let disconnectedAt = null;

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
          // WB-UI-017: the run page's QD map/metrics are server-rendered from
          // the published archive, not patched by JS -- when the published
          // revision moves on, a reload is the simplest way to pick it up.
          // root.dataset.revision only exists on the run page (not the
          // generation-job page), so this is a no-op there.
          if (root.dataset.revision !== undefined) {
            const newRevision = json.publication_revision == null ? "" : String(json.publication_revision);
            if (newRevision !== root.dataset.revision) {
              stopped = true;
              window.location.reload();
              return;
            }
          }
          if (disconnectedAt !== null) {
            const seconds = Math.round((Date.now() - disconnectedAt) / 1000);
            setText(root, "delta", `復帰（${seconds}秒ぶり）`);
            disconnectedAt = null;
          }
          if (terminalStates.has(json.state)) {
            stopped = true;
            window.location.reload();
            return;
          }
        }
      } catch (error) {
        if (disconnectedAt === null) {
          disconnectedAt = Date.now();
        }
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
      const stateSelect = row.querySelector('[data-field="state"]');
      if (stateSelect) {
        // Recolour the pill (.state-sel-*) as soon as the choice changes, so
        // ✔採用/⏸保留/✖除外/○未分類 reads correctly before 保存 is pressed.
        stateSelect.addEventListener("change", () => {
          stateSelect.className = `state-select state-sel-${stateSelect.value}`;
        });
      }
      button.addEventListener("click", async () => {
        const statusEl = row.querySelector("[data-save-status]");
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

  // WB-UI-010 Stage 2: the /worlds and /genres editor pages (§4 JS). Every
  // save/create/validate call reuses api()/collectConfig()/applyErrors()
  // above -- this only wires the extra DOM markup library_pages.py renders.
  const initLibrary = () => {
    const root = document.querySelector('[data-wb="library"]');
    if (root) {
      const kind = root.dataset.kind;
      const owner = root.dataset.owner;

      root.querySelectorAll('[data-action="save-file"]').forEach((button) => {
        button.addEventListener("click", async () => {
          const path = button.dataset.path;
          const textarea = root.querySelector(`textarea[data-file="${CSS.escape(path)}"]`);
          const statusEl = root.querySelector(`[data-save-status][data-for="${CSS.escape(path)}"]`);
          const errorEl = root.querySelector(`[data-error-for="${CSS.escape(path)}"]`);
          if (!textarea) {
            return;
          }
          button.disabled = true;
          if (errorEl) {
            errorEl.textContent = "";
          }
          try {
            const { status, json } = await api("POST", `/api/${kind}s/${encodeURIComponent(owner)}/files`, {
              path,
              content: textarea.value,
            });
            if (status === 200) {
              if (statusEl) {
                const now = new Date();
                const hh = String(now.getHours()).padStart(2, "0");
                const mm = String(now.getMinutes()).padStart(2, "0");
                statusEl.textContent = `保存しました ${hh}:${mm}`;
              }
            } else if (errorEl) {
              errorEl.textContent = (json && json.message) || `HTTP ${status}`;
            }
          } catch (error) {
            if (errorEl) {
              errorEl.textContent = "サーバーに接続できません";
            }
          } finally {
            button.disabled = false;
          }
        });
      });

      const addSubject = root.querySelector('[data-action="add-subject"]');
      if (addSubject) {
        addSubject.addEventListener("click", async () => {
          const nameInput = root.querySelector("[data-subject-name]");
          const contentArea = root.querySelector("[data-subject-content]");
          const statusEl = root.querySelector('[data-save-status][data-for="subjects-new"]');
          const errorEl = root.querySelector('[data-error-for="subjects-new"]');
          const name = (nameInput && nameInput.value.trim()) || "";
          if (!name) {
            if (errorEl) {
              errorEl.textContent = "ファイル名を入力してください";
            }
            return;
          }
          addSubject.disabled = true;
          try {
            const { status, json } = await api("POST", `/api/worlds/${encodeURIComponent(owner)}/files`, {
              path: `subjects/${name}.yaml`,
              content: contentArea ? contentArea.value : "",
            });
            if (status === 200) {
              window.location.reload();
              return;
            }
            if (errorEl) {
              errorEl.textContent = (json && json.message) || `HTTP ${status}`;
            }
          } catch (error) {
            if (errorEl) {
              errorEl.textContent = "サーバーに接続できません";
            }
          } finally {
            addSubject.disabled = false;
          }
        });
      }

      const validateForm = root.querySelector('[data-action="validate"]');
      if (validateForm) {
        validateForm.addEventListener("submit", async (event) => {
          event.preventDefault();
          const select = validateForm.querySelector('[data-field="other_id"]');
          const resultEl = validateForm.querySelector('[data-field="validation"]');
          const otherKey = kind === "world" ? "template_id" : "project_id";
          const button = validateForm.querySelector('button[type="submit"]');
          if (button) {
            button.disabled = true;
          }
          if (resultEl) {
            resultEl.classList.remove("validation-error");
          }
          try {
            const { status, json } = await api("POST", `/api/${kind}s/${encodeURIComponent(owner)}/validate`, {
              [otherKey]: select ? select.value : "",
            });
            if (!resultEl) {
              return;
            }
            resultEl.textContent = "";
            if (status === 200 && json) {
              const dl = document.createElement("dl");
              const rows = [
                ["世界名", json.world_name],
                ["主人公", json.protagonist],
                ["敵役", json.antagonist],
                ["人物数", json.subjects],
                ["結末", (json.target_endings || []).join(", ")],
                ["省略ファイル",
                  json.fallbacks && Object.keys(json.fallbacks).length
                    ? Object.keys(json.fallbacks).join(", ")
                    : "省略なし"],
              ];
              rows.forEach(([label, value]) => {
                const dt = document.createElement("dt");
                dt.textContent = label;
                const dd = document.createElement("dd");
                dd.textContent = value === undefined || value === null ? "" : String(value);
                dl.append(dt, dd);
              });
              resultEl.append(dl);
            } else {
              resultEl.classList.add("validation-error");
              const fieldErrors = (json && json.field_errors) || {};
              const rest = Object.values(fieldErrors);
              resultEl.textContent = [(json && json.message) || "", ...rest].filter(Boolean).join(" / ") || `HTTP ${status}`;
            }
          } catch (error) {
            if (resultEl) {
              resultEl.classList.add("validation-error");
              resultEl.textContent = "サーバーに接続できません";
            }
          } finally {
            if (button) {
              button.disabled = false;
            }
          }
        });
      }
    }

    document.querySelectorAll('[data-wb="library-create"]').forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const kind = form.dataset.kind;
        const button = form.querySelector('button[type="submit"]');
        if (button) {
          button.disabled = true;
        }
        try {
          const { status, json } = await api("POST", `/api/${kind}s`, collectConfig(form));
          const idKey = kind === "world" ? "world_id" : "template_id";
          if (status === 201 && json && json[idKey]) {
            window.location.href = `/${kind}s/${encodeURIComponent(json[idKey])}`;
            return;
          }
          applyErrors(form, json);
        } catch (error) {
          applyErrors(form, { message: "サーバーに接続できません" });
        } finally {
          if (button) {
            button.disabled = false;
          }
        }
      });
    });
  };

  // WB-UI-014: the "詳細" toggle on a candidate/output list row. Expanded
  // state is not preserved across a reload.
  const initRowToggles = () => {
    document.querySelectorAll(".row-toggle").forEach((button) => {
      button.addEventListener("click", () => {
        const target = document.getElementById(button.getAttribute("aria-controls") || "");
        if (!target) {
          return;
        }
        const expanded = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!expanded));
        target.hidden = expanded;
      });
    });
  };

  // WB-UI-022: mirrors execution/configs.py's quick_label() -- built here
  // (not server-rendered) so the timestamp is the actual click time, not
  // this page's render time (the page can sit open a while before a click).
  const quickLabel = (worldName, genre) => {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} `
      + `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    return `${stamp} ${genre} - ${worldName}`;
  };

  // WB-UI-022: "この世界で新しい実験を回す" buttons carry data-quick-start --
  // clicking one saves a config (auto-generated 設定名, current defaults, no
  // form) and jumps straight to the run screen. A modified click (new tab,
  // etc.) or any failure (or no JS) falls back to the anchor's own href, the
  // full /configs/new form.
  const initQuickStart = () => {
    document.querySelectorAll("[data-quick-start]").forEach((link) => {
      link.addEventListener("click", async (event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
          return;
        }
        event.preventDefault();
        if (link.dataset.busy) {
          return;
        }
        link.dataset.busy = "1";
        const projectId = link.dataset.project;
        try {
          const { status, json } = await api("POST", "/api/configs", {
            label: quickLabel(link.dataset.worldName, link.dataset.template),
            project_id: projectId,
            template_id: link.dataset.template,
          });
          if (status === 201 && json && json.config_id) {
            window.location.href =
              `/jobs?world=${encodeURIComponent(projectId)}&config=${encodeURIComponent(json.config_id)}`;
            return;
          }
        } catch (error) {
          // fall through to href below
        }
        delete link.dataset.busy;
        window.location.href = link.href;
      });
    });
  };

  // Header world picker: always lands on the world's own page (its
  // experiments list disambiguates which run to continue with).
  const initWorldPicker = () => {
    document.querySelectorAll('[data-wb="world-picker"]').forEach((select) => {
      select.addEventListener("change", () => {
        if (select.value) {
          window.location.href = `/worlds/${encodeURIComponent(select.value)}`;
        }
      });
    });
  };

  initConfigForm();
  initOutputSettings();
  initRunConfigPicker();
  initStart();
  initJob();
  initGenerate();
  initOutput();
  initCandidates();
  initTray();
  initLibrary();
  initRowToggles();
  initWorldPicker();
  initQuickStart();
})();
