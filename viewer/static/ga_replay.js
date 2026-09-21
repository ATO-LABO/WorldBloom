"use strict";

// WB-GAVIZ-001: replays the "what happened this generation" panel that
// viewer/ga_replay.py renders server-side (the <ol class="ga-replay-list">
// inside <details class="ga-replay-text"> already carries the same
// information with no JS at all, and the player track's <a> segments are
// plain links to ?gen=N). This script adds the animated SVG and turns the
// track into an in-page scrubber with generation auto-advance, all driven
// by the `data-replay` JSON attribute (CSP forbids inline scripts/JSON, so
// the panel HTML carries data, not markup, for this to read).
//
// init(root) builds one playback instance for the `.ga-replay[data-replay]`
// element `root`; it can be called again after a DOM swap (advanceTo below
// fetches the next generation's block and replaces the old one with it).
// `stopCurrent` is the only state that survives across instances -- calling
// it invalidates whatever instance is currently running (by bumping that
// instance's own runToken) before a new one takes over, so two playbacks
// can never animate at once.
(() => {
  let stopCurrent = () => {};

  const init = (root) => {
    stopCurrent();
    stopCurrent = () => {};
    if (!root) return;

    let model;
    try {
      model = JSON.parse(root.dataset.replay);
    } catch (error) {
      return; // malformed payload: the plain <ol>/track fallback already covers it
    }
    if (!model || !Array.isArray(model.individuals) || model.individuals.length === 0) {
      return;
    }

    const block = root.parentElement; // <div class="ga-replay-block" data-vessel="replay">
    // Viewing the latest generation: drop ?gen= so the live-run page reload on
    // the next publication follows the new latest instead of pinning this one.
    const managed = root.hasAttribute("data-rw-managed");
    if (!managed && model.generation === model.max_generation) {
      const url = new URL(location.href);
      if (url.searchParams.has("gen")) {
        url.searchParams.delete("gen");
        history.replaceState(null, "", url);
      }
    }

    const SAME_PARENT_NOTE = "同じ親が2回選ばれた。交叉しても変わらず、違いは突然変異だけ";
    const STEP_NAMES = ["親選び", "交叉", "突然変異", "シミュレーション", "着地", "結末"];

    const NS = "http://www.w3.org/2000/svg";
    const VIEW_W = 680;
    const VIEW_H = 256;
    const ROWS_Y = [6, 80, 154];
    const ROW_H = 66;
    const BAR_X0 = 60;
    const BAR_AREA_W = 370;
    const BAR_W = 30;
    const GENE_COUNT = model.gene_ranges.length;
    const BAR_GAP = GENE_COUNT > 1 ? (BAR_AREA_W - GENE_COUNT * BAR_W) / (GENE_COUNT - 1) : 0;
    const MAP_X0 = 472;
    const MAP_Y0 = 26;
    const MAP_W = 670 - MAP_X0;
    const MAP_H = 236 - MAP_Y0;
    const ORIGIN_LETTER = { a: "A", b: "B", mut: "変" };
    const ORIGIN_VAR = { a: "var(--replay-a)", b: "var(--replay-b)", mut: "var(--replay-mut)", same: "var(--line)" };
    const PARENT_OUTLINE_COLORS = ["var(--replay-a)", "var(--replay-b)"];

    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const normalize = (value, index) => {
      const [lo, hi] = model.gene_ranges[index];
      if (typeof value !== "number" || hi === lo) return 0;
      return Math.min(1, Math.max(0, (value - lo) / (hi - lo)));
    };

    const el = (tag, attrs) => {
      const node = document.createElementNS(NS, tag);
      for (const key in attrs) node.setAttribute(key, attrs[key]);
      return node;
    };

    // ------------------------------------------------------------ DOM shell
    const svg = el("svg", { viewBox: `0 0 ${VIEW_W} ${VIEW_H}`, role: "img", "aria-hidden": "true" });
    root.appendChild(svg);

    // The step-by-step caption changes several times per individual -- not
    // a live region, or a screen reader would narrate every intermediate
    // step. The outcome sentence (what actually matters) instead goes to
    // this separate, visually-hidden region exactly once per individual,
    // plus once more for the closing line.
    const caption = document.createElement("p");
    caption.className = "ga-replay-caption";
    root.appendChild(caption);

    const srStatus = document.createElement("p");
    srStatus.className = "ga-replay-sr";
    srStatus.setAttribute("aria-live", "polite");
    root.appendChild(srStatus);

    // Reduced-motion's own controls (kept where they always were, under the
    // SVG); the animated path's play/pause/replay button lives in the
    // player row's label cell instead (see playerButton below) and never
    // touches this element.
    const controls = document.createElement("div");
    controls.className = "ga-replay-controls";
    root.appendChild(controls);

    const announce = (sentence) => {
      caption.textContent = sentence;
      srStatus.textContent = sentence;
    };

    const rowTitle = (y, text) => {
      const label = el("text", { x: 5, y: y + ROW_H / 2, "font-size": "13" });
      label.textContent = text;
      svg.appendChild(label);
    };
    rowTitle(ROWS_Y[0], "親A");
    rowTitle(ROWS_Y[1], "親B");
    rowTitle(ROWS_Y[2], "子");

    // 9-bar group for one genome row; returns setters used during playback.
    const buildRow = (rowY, withUnderLabels) => {
      const baseline = rowY + ROW_H - 10;
      const bars = [];
      for (let i = 0; i < GENE_COUNT; i++) {
        const x = BAR_X0 + i * (BAR_W + BAR_GAP);
        const track = el("rect", {
          x, y: rowY + 5, width: BAR_W, height: baseline - (rowY + 5),
          fill: "var(--line)", "fill-opacity": "0.4",
        });
        svg.appendChild(track);
        const bar = el("rect", {
          x, y: baseline, width: BAR_W, height: 0, fill: "var(--muted)",
          style: "transition: height 350ms ease, y 350ms ease, fill 200ms ease",
        });
        svg.appendChild(bar);
        bars.push({ bar, x, baseline });
        if (withUnderLabels) {
          const short = el("text", {
            x: x + BAR_W / 2, y: baseline + 12, "font-size": "12",
            "text-anchor": "middle", fill: "var(--muted)",
          });
          short.textContent = model.gene_short[i] || "";
          if (model.gene_labels && model.gene_labels[i]) {
            const title = document.createElementNS(NS, "title");
            title.textContent = model.gene_labels[i];
            short.appendChild(title);
          }
          svg.appendChild(short);
          const origin = el("text", {
            x: x + BAR_W / 2, y: baseline + 29, "font-size": "12",
            "text-anchor": "middle", "font-weight": "bold",
          });
          svg.appendChild(origin);
          bars[i].originLabel = origin;
        }
      }
      return bars;
    };

    const rowA = buildRow(ROWS_Y[0], false);
    const rowB = buildRow(ROWS_Y[1], false);
    const rowChild = buildRow(ROWS_Y[2], true);

    const setBar = (row, index, value, colorVar) => {
      const height = normalize(value, index) * (ROW_H - 25);
      const { bar, baseline } = row[index];
      bar.setAttribute("height", String(height));
      bar.setAttribute("y", String(baseline - height));
      if (colorVar) bar.setAttribute("fill", colorVar);
    };

    const clearRow = (row) => {
      row.forEach((entry) => {
        entry.bar.setAttribute("height", "0");
        entry.bar.setAttribute("y", String(entry.baseline));
        if (entry.originLabel) entry.originLabel.textContent = "";
      });
    };

    // ------------------------------------------------------------ mini map
    const categories = model.categories;
    const bins = model.bins;
    const cellW = bins.length ? MAP_W / bins.length : MAP_W;
    const cellH = categories.length ? MAP_H / categories.length : MAP_H;
    const cellKey = (category, bin) => `${category}|${bin}`;
    const cellRects = {};

    bins.forEach((bin, col) => {
      const label = el("text", {
        x: MAP_X0 + col * cellW + cellW / 2, y: MAP_Y0 - 4,
        "font-size": "12", "text-anchor": "middle", fill: "var(--muted)",
      });
      label.textContent = bin;
      svg.appendChild(label);
    });
    categories.forEach((category, row) => {
      const label = el("text", {
        x: MAP_X0 - 4, y: MAP_Y0 + row * cellH + cellH / 2 + 3,
        "font-size": "12", "text-anchor": "end", fill: "var(--muted)",
      });
      label.textContent = category;
      svg.appendChild(label);
      bins.forEach((bin, col) => {
        const rect = el("rect", {
          x: MAP_X0 + col * cellW, y: MAP_Y0 + row * cellH,
          width: Math.max(0, cellW - 2), height: Math.max(0, cellH - 2),
          fill: "var(--line)", "fill-opacity": "0.35",
          style: "transition: fill-opacity 400ms ease, stroke-opacity 400ms ease",
          stroke: "var(--accent)", "stroke-opacity": "0", "stroke-width": "2",
        });
        svg.appendChild(rect);
        cellRects[cellKey(category, bin)] = rect;
      });
    });

    // `quality` a number paints that value; `undefined` leaves the current
    // fill alone (used for a "rejected" landing, which highlights a cell
    // without being allowed to overwrite the incumbent's true value -- see
    // the M1 fix in playOne/drawStatic below). `highlight` only ever touches
    // the stroke, never the fill.
    const paintCell = (key, quality, { highlight } = {}) => {
      const rect = cellRects[key];
      if (!rect) return;
      if (typeof quality === "number") {
        rect.setAttribute("fill", "var(--accent)");
        rect.setAttribute("fill-opacity", String(0.15 + 0.75 * Math.min(1, Math.max(0, quality))));
      }
      rect.setAttribute("stroke-opacity", highlight ? "0.9" : "0");
    };

    // What is actually painted right now, keyed by cell -- lets applyStage
    // (below) skip re-writing a cell whose value has not changed, instead of
    // blanking then repainting the whole map on every individual (a
    // fill-opacity transition on an unchanged value still looks like a
    // flicker if it is retriggered for no reason).
    const stageCells = {};

    const landCell = (key, quality, opts) => {
      paintCell(key, quality, opts);
      if (typeof quality === "number") stageCells[key] = quality;
    };

    // Repaints the map to `targetCells` ({cell_key: quality}), touching only
    // the cells whose value actually differs from what is on screen.
    const applyStage = (targetCells) => {
      Object.keys(cellRects).forEach((key) => {
        const target = Object.prototype.hasOwnProperty.call(targetCells, key) ? targetCells[key] : undefined;
        if (stageCells[key] !== target) {
          if (typeof target === "number") {
            paintCell(key, target, { highlight: false });
          } else {
            const rect = cellRects[key];
            rect.setAttribute("fill", "var(--line)");
            rect.setAttribute("fill-opacity", "0.35");
            rect.setAttribute("stroke-opacity", "0");
          }
          stageCells[key] = target;
        } else {
          cellRects[key].setAttribute("stroke-opacity", "0"); // clear a stray highlight, no fill write
        }
      });
    };

    // The mini-map an individual starts against: the generation's own
    // starting point (prev_cells, or nothing when unknown) plus whatever
    // earlier representatives in this replay already landed -- otherwise
    // "new" vs "replaced" is invisible (both just look like an empty map).
    // Only EARLIER representatives (index < this one) ever contribute, and
    // always via final_cells (the true, settled archive value for that cell)
    // rather than that representative's own outcome.quality -- a "rejected"
    // representative's own quality is the losing one, not the cell's real
    // value (M1).
    const baselineCells = (index) => {
      const merged = Object.assign({}, model.prev_cells || {});
      for (let i = 0; i < index; i++) {
        const prior = model.individuals[i];
        if (prior.cell_key && typeof model.final_cells[prior.cell_key] === "number") {
          merged[prior.cell_key] = model.final_cells[prior.cell_key];
        }
      }
      return merged;
    };

    let parentOutlineEls = [];
    const clearParentOutlines = () => {
      parentOutlineEls.forEach((node) => node.remove());
      parentOutlineEls = [];
    };

    // Outlines (not fill -- fill is reserved for quality) the cell each known
    // parent came from, in that parent's colour. Two parents sharing a cell
    // still get two distinguishable outlines (B's is inset and dashed).
    const outlineParentCells = (parents) => {
      clearParentOutlines();
      (parents || []).forEach((parent, slot) => {
        if (!parent || !parent.cell_key) return;
        const rect = cellRects[parent.cell_key];
        if (!rect) return;
        const inset = slot * 3;
        const outline = el("rect", {
          x: Number(rect.getAttribute("x")) + inset,
          y: Number(rect.getAttribute("y")) + inset,
          width: Math.max(0, Number(rect.getAttribute("width")) - inset * 2),
          height: Math.max(0, Number(rect.getAttribute("height")) - inset * 2),
          fill: "none",
          stroke: PARENT_OUTLINE_COLORS[slot] || PARENT_OUTLINE_COLORS[0],
          "stroke-width": "2",
        });
        if (slot === 1) outline.setAttribute("stroke-dasharray", "3,2");
        svg.appendChild(outline);
        parentOutlineEls.push(outline);
      });
    };

    // Common reset before drawing/playing any one individual (both the
    // animated and the reduced-motion static path) so frames never
    // accumulate across individuals.
    const prepareStage = (individual, index) => {
      clearRow(rowA);
      clearRow(rowB);
      clearRow(rowChild);
      applyStage(baselineCells(index));
      outlineParentCells(individual.parents);
    };

    // ---------------------------------------------------- player row (P4/P5)
    // The track/label/position elements ga_replay.py rendered as the first
    // row of the block, one level up from `root`.
    const playerLabel = block && block.querySelector(".ga-replay-player-label");
    const trackSegments = block ? Array.from(block.querySelectorAll(".ga-replay-track .ga-replay-gen")) : [];
    const currentSegment = trackSegments[model.generation];
    const currentFill = currentSegment && currentSegment.querySelector(".ga-replay-gen-fill");
    const posEl = block && block.querySelector(".ga-replay-pos");
    const totalSteps = model.individuals.length * STEP_NAMES.length;

    const resetProgress = () => {
      if (currentFill) currentFill.style.width = "0%";
    };
    // "個体 2/4・交叉" sits in the block head, not in the player row: the row's
    // third column is as narrow as the other progress bars' and must stay on
    // one line (posEl keeps the server-rendered "第 N 世代 / M").
    const headEl = block && block.querySelector(".ga-replay-head");
    let stepEl = null;
    if (headEl && !reduceMotion) {
      stepEl = document.createElement("span");
      stepEl.className = "ga-replay-step";
      headEl.insertBefore(stepEl, headEl.querySelector(".vhead-sub"));
    }
    const setProgress = (i, step) => {
      if (currentFill && totalSteps) {
        const pct = ((i * STEP_NAMES.length + step + 1) / totalSteps) * 100;
        currentFill.style.width = `${pct}%`;
      }
      if (stepEl) stepEl.textContent = `個体 ${i + 1}/${model.individuals.length}・${STEP_NAMES[step]}`;
    };
    const setProgressDone = () => {
      if (currentFill) currentFill.style.width = "100%";
      if (stepEl) stepEl.textContent = "完了";
    };

    let playerButton = null;
    if (playerLabel && !reduceMotion) {
      playerButton = document.createElement("button");
      playerButton.type = "button";
      playerButton.className = "button ga-replay-player-btn";
      // The button replaces the server-rendered "リプレイ" word: both do not
      // fit the 7.5em label column the other progress rows use.
      playerLabel.textContent = "";
      playerLabel.appendChild(playerButton);
    }

    const setPlayerLabel = label => {
      if (!playerButton) return;
      playerButton.setAttribute("aria-label", label);
      playerButton.title = label;
      if (!managed) {playerButton.textContent = label; return;}
      const shape = label === "再生を一時停止" ? '<path d="M7 5h3v14H7zM14 5h3v14h-3z"/>' : label === "もう一度" ? '<path d="M5 9a8 8 0 1 1-1 7M5 3v6h6" fill="none" stroke="currentColor" stroke-width="2"/>' : '<path d="M8 5l11 7-11 7z"/>';
      playerButton.innerHTML = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">${shape}</svg>`;
    };

    // ------------------------------------------------------------ playback
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    let paused = managed && root.dataset.active !== "true";
    let speed = 1;
    let runToken = 0;
    stopCurrent = () => {
      runToken += 1;
      document.removeEventListener("visibilitychange", onVisibility);
    };
    const onVisibility = () => { if (document.hidden && managed) root.wbPause(); };
    document.addEventListener("visibilitychange", onVisibility);
    root.wbPause = () => { paused = true; if (playerButton && !root.dataset.finished) setPlayerLabel("再生"); };
    root.wbSpeed = (value) => { speed = Math.min(4, Math.max(0.5, Number(value) || 1)); };
    root.wbIsPaused = () => paused;

    // Counts down `ms` of *visible, unpaused* time: time spent hidden or
    // paused does not consume the step's budget. `token` is this instance's
    // playback generation -- if a newer instance has taken over (runToken
    // moved on, e.g. via advanceTo/stopCurrent), this bails immediately
    // instead of a stale loop fighting a fresh one.
    const wait = async (ms, token) => {
      let remaining = ms;
      while (remaining > 0) {
        if (token !== runToken) return;
        if (document.hidden || paused) {
          await sleep(100);
          continue;
        }
        const chunk = Math.min(100, remaining);
        await sleep(chunk);
        if (token !== runToken) return;
        remaining -= chunk * speed;
      }
    };

    const outcomeSentence = (outcome) => {
      const q = (value) => (typeof value === "number" ? value.toFixed(3) : "—");
      switch (outcome.kind) {
        case "new":
          return `新しい型の物語を発見。地図が広がる（q ${q(outcome.quality)}）`;
        case "kept":
          return `地図に残った（q ${q(outcome.quality)}）`;
        case "replaced":
          return `同じ型でより良い物語。入れ替える（旧 ${q(outcome.prev_quality)} → 新 ${q(outcome.quality)}）`;
        case "rejected":
          return `同じ型にもっと良い物語がある。今回は残らない（この個体 ${q(outcome.quality)} ・既存 ${q(outcome.incumbent_quality)}）`;
        case "unclassified":
          return "結末には届いたが、地図の型に分類できなかった。地図には載らない";
        default:
          return "決められた結末にたどり着けなかった。地図には載らない";
      }
    };

    // A "rejected" individual never gets to overwrite its cell's displayed
    // value with its own (losing) quality -- only the highlight (M1).
    const landingQuality = (outcome) => (outcome.kind === "rejected" ? undefined : outcome.quality);

    const samePairCaption = (parents) => `親A・親Bとも ${parents[0].display}（${SAME_PARENT_NOTE}）`;

    const drawStatic = (individual, index, step = 5) => {
      prepareStage(individual, index);
      const parents = individual.parents || [];
      if (parents[0] && parents[0].genes) parents[0].genes.forEach((v, i) => setBar(rowA, i, v, "var(--replay-a)"));
      if (parents[1] && parents[1].genes) parents[1].genes.forEach((v, i) => setBar(rowB, i, v, "var(--replay-b)"));
      if (step >= 1) individual.genes.forEach((v, i) => {
        const origin = individual.origins[i];
        const revealed = origin !== "mut" || step >= 2;
        setBar(rowChild, i, v, revealed ? (ORIGIN_VAR[origin] || "var(--muted)") : "var(--muted)");
        if (rowChild[i].originLabel) rowChild[i].originLabel.textContent = revealed ? (ORIGIN_LETTER[origin] || "") : "";
      });
      if (step >= 4 && individual.cell_key) landCell(individual.cell_key, landingQuality(individual.outcome), { highlight: true });
      setProgress(index, step);
      const descriptions = [parents.length ? "記録された親の特徴を確認します。" : "親なしの新顔です。", "子の特徴を確認します。灰色は親由来と確定できない値です。", "どちらの親とも異なる値を示します。", "保存されたシミュレーション結果をたどります。", individual.cell_key ? "記録された地図の型を示します。" : "この個体は地図に載りませんでした。", outcomeSentence(individual.outcome)];
      announce(`個体 #${individual.index}・${STEP_NAMES[step]}: ${descriptions[step]}`);
    };

    // The story travelling from the child genome to the map. Appended last so
    // it paints above the bars and cells.
    const DOT_START_X = BAR_X0 + BAR_AREA_W + 18;
    const DOT_START_Y = ROWS_Y[2] + ROW_H / 2;
    const dot = el("circle", { r: 6, cx: 0, cy: 0, fill: "currentColor", style: "opacity: 0" });
    svg.appendChild(dot);
    const placeDot = (x, y, instant) => {
      dot.style.transition = instant ? "none" : "transform 900ms ease-in-out, opacity 300ms ease 600ms";
      dot.style.transform = `translate(${x}px, ${y}px)`;
      if (instant) {
        dot.getBoundingClientRect(); // flush, so the next move animates from here
        dot.style.opacity = "1";
      }
    };

    const playOne = async (individual, index, token) => {
      dot.style.transition = "none";
      dot.style.opacity = "0";
      prepareStage(individual, index);
      const parents = individual.parents || [];
      const hasParents = parents.length === 2;
      const samePair = hasParents && parents[0].label === parents[1].label;

      if (hasParents) {
        setProgress(index, 0); // 親選び
        caption.textContent = samePair
          ? samePairCaption(parents)
          : `親を2つ選ぶ: 親A ${parents[0].display} × 親B ${parents[1].display}`;
        if (parents[0].genes) parents[0].genes.forEach((v, i) => setBar(rowA, i, v, "var(--replay-a)"));
        if (parents[1].genes) parents[1].genes.forEach((v, i) => setBar(rowB, i, v, "var(--replay-b)"));
        await wait(1200, token);
        if (token !== runToken) return;

        // V3: a mutated gene grows in a neutral colour with no letter here --
        // crossover only ever copies from a parent, so claiming a colour/
        // letter for a gene that in fact mutated would be a lie. It only
        // becomes visibly "different" in the dedicated mutation step below.
        setProgress(index, 1); // 交叉
        caption.textContent = samePair ? samePairCaption(parents) : "交叉: 子の遺伝子が親から1本ずつ伸びる";
        for (let i = 0; i < GENE_COUNT; i++) {
          const origin = individual.origins[i];
          const revealDuringCrossover = origin !== "mut";
          setBar(rowChild, i, individual.genes[i], revealDuringCrossover ? (ORIGIN_VAR[origin] || "var(--muted)") : "var(--muted)");
          if (rowChild[i].originLabel) {
            rowChild[i].originLabel.textContent = revealDuringCrossover ? (ORIGIN_LETTER[origin] || "") : "";
          }
          await wait(150, token);
          if (token !== runToken) return;
        }
        await wait(700, token);
        if (token !== runToken) return;

        const hasMutation = individual.origins.includes("mut");
        if (hasMutation) {
          setProgress(index, 2); // 突然変異
          caption.textContent = "突然変異: どちらの親とも違う値が生まれた";
          individual.origins.forEach((origin, i) => {
            if (origin !== "mut") return;
            setBar(rowChild, i, individual.genes[i], ORIGIN_VAR.mut);
            if (rowChild[i].originLabel) rowChild[i].originLabel.textContent = ORIGIN_LETTER.mut;
          });
          await wait(900, token);
          if (token !== runToken) return;
        }
      } else {
        // Parentless: steps 0-2 never happen, but the *positions* they would
        // have occupied are not renumbered -- setProgress(index, 3) below
        // lands at the same fraction of the bar a two-parent individual's
        // simulate step would (P5).
        individual.genes.forEach((v, i) => setBar(rowChild, i, v, "var(--muted)"));
        caption.textContent = "親なしの新顔。ランダムな性格から始める";
        await wait(1400, token);
        if (token !== runToken) return;
      }

      setProgress(index, 3); // シミュレーション
      caption.textContent = managed ? "保存されたシミュレーション結果をたどる…" : "シミュレーション実行中…";
      placeDot(DOT_START_X, DOT_START_Y, true);
      await wait(1200, token);
      if (token !== runToken) return;

      setProgress(index, 4); // 着地
      const landed = individual.cell_key && cellRects[individual.cell_key];
      if (landed) {
        caption.textContent = "着地: 生まれた物語が地図のマスへ移動する";
        placeDot(
          Number(landed.getAttribute("x")) + Number(landed.getAttribute("width")) / 2,
          Number(landed.getAttribute("y")) + Number(landed.getAttribute("height")) / 2,
        );
      } else {
        caption.textContent = "着地: 決められた結末に届かず、地図の外へ落ちて消える";
        placeDot(DOT_START_X, VIEW_H + 12);
        dot.style.opacity = "0";
      }
      await wait(1000, token);
      if (token !== runToken) return;
      if (landed) landCell(individual.cell_key, landingQuality(individual.outcome), { highlight: true });
      dot.style.transition = "opacity 200ms ease";
      dot.style.opacity = "0";

      setProgress(index, 5); // 結末
      announce(`個体 #${individual.index}: ${outcomeSentence(individual.outcome)}`);
      await wait(3000, token); // V1: hold the outcome long enough to actually read it
    };

    // V2: reads the panel's own summary line instead of re-deriving counts
    // in JS -- .replay-summary is rendered by ga_replay.py as a sibling of
    // this .ga-replay element, inside the same block.
    const closingLine = () => {
      const summaryEl = block && block.querySelector(".replay-summary");
      const summaryText = summaryEl ? summaryEl.textContent : "";
      return `第 ${model.generation + 1} 世代はここまで。${summaryText}`;
    };

    const bloomRemaining = async (token) => {
      const shown = new Set(model.individuals.filter((i) => i.cell_key).map((i) => i.cell_key));
      const remaining = Object.keys(model.final_cells).filter((key) => !shown.has(key));
      announce(remaining.length
        ? "この世代で変わった残りのマスをまとめて塗る"
        : "この世代の再生が終わりました");
      clearParentOutlines();
      // Always paint every final cell, not just the ones no representative
      // already touched: a "rejected" representative's landing step no longer
      // overwrites its cell (M1), but this pass is still the single place
      // that guarantees the map matches the true archive once the replay ends.
      Object.keys(model.final_cells).forEach((key) => landCell(key, model.final_cells[key]));
      Object.keys(cellRects).forEach((key) => paintCell(key, undefined, { highlight: false }));
      await wait(reduceMotion ? 0 : 600, token);
      if (token !== runToken) return;
      announce(closingLine());
    };

    // ---------------------------------------------------- fetch + swap (P6)
    // Same-origin fetch of another generation's page and adoption of its
    // replay block, so the run page never reloads to move between
    // generations. On ANY failure this does nothing further -- the caller
    // is left showing its own finished state (the もう一度 button).
    const advanceTo = async (g) => {
      if (managed) {
        root.dispatchEvent(new CustomEvent("rw-replay-generation", {bubbles: true, detail: g}));
        return true;
      }
      const token = runToken;
      let response;
      try {
        response = await fetch(location.pathname + "?gen=" + g);
      } catch (error) {
        return;
      }
      if (token !== runToken || !response.ok) return;
      let text;
      try {
        text = await response.text();
      } catch (error) {
        return;
      }
      if (token !== runToken) return;
      let doc;
      try {
        doc = new DOMParser().parseFromString(text, "text/html");
      } catch (error) {
        return;
      }
      const fetchedBlock = doc.querySelector('[data-vessel="replay"]');
      const fetchedInner = fetchedBlock && fetchedBlock.querySelector(".ga-replay[data-replay]");
      if (!fetchedBlock || !fetchedInner) return;
      let fetchedModel;
      try {
        fetchedModel = JSON.parse(fetchedInner.dataset.replay);
      } catch (error) {
        return;
      }
      if (token !== runToken || !block || !block.isConnected) return;

      const nextUrl = new URL(location.href);
      if (g === fetchedModel.max_generation) {
        nextUrl.searchParams.delete("gen");
      } else {
        nextUrl.searchParams.set("gen", String(g));
      }

      const adopted = document.adoptNode(fetchedBlock);
      block.replaceWith(adopted);
      history.replaceState(null, "", nextUrl.pathname + nextUrl.search + nextUrl.hash);
      init(adopted.querySelector(".ga-replay[data-replay]"));
      return true;
    };

    // ------------------------------------------------------------ controls
    const setPauseButton = () => {
      if (!playerButton) return;
      setPlayerLabel(paused ? "再生" : "再生を一時停止");
      playerButton.onclick = () => {
        paused = !paused;
        setPlayerLabel(paused ? "再生" : "再生を一時停止");
      };
    };

    const setReplayButton = () => {
      if (!playerButton) return;
      setPlayerLabel("もう一度");
      playerButton.onclick = () => {
        paused = false;
        setPauseButton();
        runPlayback();
      };
    };

    const runPlayback = async () => {
      delete root.dataset.finished;
      // Bumping runToken invalidates any loop still in flight (its captured
      // `token` stops matching runToken at its very next wait()), so two
      // playbacks can never animate at once even if this is somehow called
      // again before a previous run finished.
      const token = ++runToken;
      resetProgress();
      setPauseButton();
      for (let i = 0; i < model.individuals.length; i++) {
        if (token !== runToken) return;
        await playOne(model.individuals[i], i, token);
      }
      if (token !== runToken) return;
      await bloomRemaining(token);
      if (token !== runToken) return;
      setProgressDone();
      if (managed) {
        root.dataset.finished = "true";
        setReplayButton();
        root.dispatchEvent(new CustomEvent("rw-replay-ended", {bubbles: true, detail: model.generation}));
        return;
      }
      if (model.generation < model.max_generation) {
        await wait(2000, token);
        if (token !== runToken) return;
        if (await advanceTo(model.generation + 1)) return;
        if (token !== runToken) return;
      }
      setReplayButton(); // last generation, or the advance failed
    };

    const showStaticControls = () => {
      let index = 0;
      let step = 0;
      controls.textContent = "";
      const button = (label) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "button";
        b.textContent = label;
        controls.appendChild(b);
        return b;
      };
      const prev = button("前の個体");
      const next = button("次の個体");
      const prevStep = button("前の工程");
      const nextStep = button("次の工程");
      const render = () => {
        drawStatic(model.individuals[index], index, step);
        prev.disabled = index === 0;
        next.disabled = index === model.individuals.length - 1;
        prevStep.disabled = step === 0;
        nextStep.disabled = step === STEP_NAMES.length - 1;
      };
      prevStep.addEventListener("click", () => {step = Math.max(0, step-1); render();});
      nextStep.addEventListener("click", () => {step = Math.min(STEP_NAMES.length-1, step+1); render();});
      prev.addEventListener("click", () => {
        index = Math.max(0, index - 1);
        render();
      });
      next.addEventListener("click", () => {
        index = Math.min(model.individuals.length - 1, index + 1);
        render();
      });
      render();
    };

    if (reduceMotion) {
      // No fill animation, no auto-advance -- the track's <a> segments stay
      // plain links (a click reloads the page at that generation).
      showStaticControls();
    } else {
      trackSegments.forEach((segment, k) => {
        segment.addEventListener("click", (event) => {
          event.preventDefault();
          advanceTo(k); // works mid-playback: advanceTo/init stop this instance first
        });
      });
      runPlayback();
    }
  };

  window.WorldBloomReplay = { mount: init, stop: () => stopCurrent() };
  init(document.querySelector(".ga-replay[data-replay]"));
})();
