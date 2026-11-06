---
ja_rev: "e75e12c5e28f"
---

# Quickstart (one lap with Momotaro)

This walkthrough uses the current source build started with `--control` to complete one full lap with the Momotaro world. For the Windows Studio download, follow the screens and samples included in its specific Release.

## 1. Pick a world

On launch, choose "**物語制作** (Story creation)" under "**今日は何を進めますか** (What would you like to work on today?)" to reach the "**世界を選ぶ** (Pick a world)" screen. Open the bundled “**桃太郎＋3（新形式）**” (Momotaro +3, new format), stored at `projects/momotaro_plus3_new`. To create a new one, choose “**新しい世界を作成**” and follow the [three-stage draft workflow](../usage/create-world.md). Existing-world cards show the overview, character/place counts, duration and roles.

## 2. Run settings

Review the world settings, then use “**実行条件を決める →**” (Decide run conditions) in the footer. Fill in any missing required settings first.

On the "**実行設定** (Run settings)" screen (subtitle: "探索する条件を決める", "Decide what to explore"), set the exploration size — generations, population, seed count — and the run's time limit. Click "**保存して実行条件へ →** (Save and continue →)" to proceed.

## 3. Run

Confirm the settings and start the run; progress (generation, completed individuals, an ETA) is shown while it runs. With κ=0, no AI rationality judging takes place. With κ>0, the chosen judge (Ollama or Jev) needs a working connection. This is separate from synopsis/prose generation.

## 4. Results (QD map) and Sifting

When the run finishes, you see the QD map: a grid of leading category × volatility. Click a cell to open a candidate's detail, with three tabs: "**物語** (Story)", "**選択とメモ** (Choices & notes)", "**実験データ** (Run data)". Adopt candidates you like; the "**採用候補を確認（N件） →** (Review adopted candidates (N) →)" button at the bottom becomes clickable once you have at least one.

## 5. Generate synopsis/text (optional, needs an LLM)

From the adopted-candidates review screen, proceeding to generation opens a "**生成内容を確認** (Review what will be generated)" dialog; click "**生成を開始** (Start generation)" to run it. First check the saved generation backend under ⚙ 全体設定 (Global settings) → AIの設定 (AI settings). You can select and save Gemma 4 on that screen; configure other models following [LLM Backends](../usage/llm-backends.md). This step is optional: you can simply browse the grid and saved action logs.

## Running the same lap from the CLI

```
python scripts/evolve.py --project projects/momotaro_plus3_new --template templates/momotaro_plus3 \
  --out <output dir>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

**Historical measurement (not a timing guarantee for the new-format example above)**: a small run (`--generations 5 --population 30 --seeds 2 --processes 8`, i.e. 5×30×2 = 300 simulations) took 37 seconds and left 3 occupied cells on the QD map (2026-09-25, development machine). Runs that are too small may leave no candidates at all. Time scales roughly with generations × population × seeds.

For generating synopses/text from the CLI as well, see [CLI Workflow](../usage/cli-workflow.md).
