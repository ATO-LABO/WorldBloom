---
ja_rev: "30c3da611397"
---

# Quickstart (one lap with Momotaro)

Using the Studio build (or the source build started with `--control`), this walks through one full lap with the Momotaro world.

## 1. Pick a world

On launch you see the "**世界を選ぶ** (Pick a world)" home screen. Click the bundled "**桃太郎** (Momotaro)" world ("桃太郎＋" and "桃太郎＋2" are extended variants). To create a new one, use "**＋ 新しい世界を作る** (+ Create a new world)" and enter a name and overview.

## 2. Run settings

On the "**実行設定** (Run settings)" screen (subtitle: "探索する条件を決める", "Decide what to explore"), set the exploration size — generations, population, seed count — and the run's time limit. Click "**保存して実行条件へ →** (Save and continue →)" to proceed.

## 3. Run

Confirm the settings and start the run; progress (generation, completed individuals, an ETA) is shown while it runs. The GA step itself never uses an LLM, so no generation backend is needed yet.

## 4. Results (QD map) and Sifting

When the run finishes, you see the QD map: a grid of leading category × volatility. Click a cell to open a candidate's detail, with three tabs: "**物語** (Story)", "**選択とメモ** (Choices & notes)", "**実験データ** (Run data)". Adopt candidates you like; the "**採用候補を確認（N件） →** (Review adopted candidates (N) →)" button at the bottom becomes clickable once you have at least one.

## 5. Generate synopsis/text (optional, needs an LLM)

From the adopted-candidates review screen, proceeding to generation opens a "**生成内容を確認** (Review what will be generated)" dialog; click "**生成を開始** (Start generation)" to run it. If you haven't chosen an LLM backend and model in ⚙ 全体設定 (Global settings) yet, this step fails here — set that up first, see [LLM Backends](../usage/llm-backends.md). This step is optional: browsing the grid and synopses alone is a complete use case.

## Running the same lap from the CLI

```
python scripts/evolve.py --project projects/momotaro --template templates/momotaro \
  --out <output dir>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

**Measured**: a small run (`--generations 5 --population 30 --seeds 2 --processes 8`, i.e. 5×30×2 = 300 simulations) took 37 seconds and left 3 occupied cells on the QD map (2026-09-25, development machine). Runs that are too small may leave no candidates at all. Time scales roughly with generations × population × seeds.

For generating synopses/text from the CLI as well, see [CLI Workflow](../usage/cli-workflow.md).
