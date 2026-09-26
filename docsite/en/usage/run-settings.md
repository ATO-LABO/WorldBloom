---
ja_rev: "122a2f0a5f52"
---
# Run Settings

This screen opens from "実行条件を決める →" (Decide run conditions →) on the world settings screen, or right after finishing "＋ 新しい世界を作る" (+ Create a new world) on the home screen. Here you decide how a single GA experiment run should behave, save it as a "config", and run it on the next screen. The GA experiment itself (candidate generation, weight computation, evolution) never calls an LLM. However, raising rationality κ (04. below) above 0 makes the run call a local judgment LLM (Ollama) for every GA decision — at least tens of seconds per run (one individual × seed pair), and it uses the GPU. Synopsis/text generation is a separate stage (see [Generate Text](generate-text.md)).

When creating a new one, the config name is auto-filled once the world and genre are chosen (e.g. `2026-09-25 10:00 detective - 星を運ぶ街`; the genre part is the genre ID). You can also duplicate a previously saved config (the world and genre stay fixed when duplicating).

## 01. World and ending

- **世界 (World)**: pick the world to run against
- **目指す結末 (Target ending)**: by default, uses the world's configured "目標の結末" (target ending). Choosing "別の結末を指定" (Specify a different ending) lets you list ending IDs comma-separated; reaching any one of them counts as a success

## 02. Scope of exploration

| Item | Meaning | Default |
|---|---|---|
| 世代数 (Generations) | Number of GA generations | 20 |
| 1世代の個体数 (Population per generation) | Individuals per generation (population) | 100 |
| 個体ごとの評価回数 (Evaluations per individual) | Number of seeds used to evaluate one individual (seeds) | 3 |

Runs per generation is "population × seeds". More generations leave behind more good storylines, but also take proportionally longer to run.

## 03. Saving and evolution

**結果の保存 (keep)** has three options:

| Value | Contents |
|---|---|
| すべての結果 (All results) | all |
| 結末に到達した結果 (Results that reached the ending) | reached (default) |
| 個体ごとに代表1件 (One representative per individual) | exemplar |

If no individual reached the ending at all, the best individual's result is saved regardless of the `keep` setting.

**世界の拡張 (world_expansion)** (corresponds to [world self-expansion](create-world.md#world-expansion)):

| Value | Contents |
|---|---|
| しない (Off, default) | off. Leaves the world unchanged |
| 検知のみ (Detect only) | detect. Leaves the world unchanged; only tallies where actions came up empty |
| 承認済みの拡張を適用 (Apply approved expansions) | expand. Applies approved patches (`projects/<world>/patches/*.yaml`) before the run, then does the same tally |

**前の実験から引き継ぐ (Carry over from a previous experiment, evolution.seed_genomes)**: pick a completed prior experiment for the same world and genre, and only the personalities (genomes) of the individuals occupying its final map are carried into this experiment's generation 0. No precedent table, map, or volatility thresholds are carried over -- this experiment rebuilds them from scratch starting at generation 0. The default is "don't carry over", which stays byte-identical to before this feature.

Other toggles:

- **説明記録 (record_explanations, default on)**: records candidates' choice/rationale/cost/turning point so they can be reviewed later
- **共進化 (coevolve, default off)**: evolves the antagonist's population in parallel too. Turning this on doubles the evaluation count (protagonist path + antagonist path)
- **メタ進化 (meta_evolution, default off)**: in addition to the 9 numeric genes, also searches over enabling/disabling each rule

**世界を育てる (Grow the world, growth.mode, default "off")**: repeats the run automatically as an "epoch chain" to grow the world itself little by little. An epoch is one cycle of growth — one experiment run, followed by proposing/checking/approving an expansion for a place the world came up short, then retiring any expansion that went unused — and the chain carries the previous epoch's personalities (the genomes of the individuals occupying its map) into the next epoch's generation 0. Choosing anything other than "off" reveals "何周回すか" (How many epochs, growth.epochs, 1-10, default 3) and "使われなかった拡張を自動で枯らす" (Automatically retire unused expansions, growth.auto_retire, default on for auto mode / off for manual mode), and forces 世界の拡張 (world_expansion) to "承認済みの拡張を適用" (Apply approved expansions). "自動で育てる" (Auto) also auto-approves and auto-retires whenever the proposal's holdout check looks good; "手動で育てる" (Manual) instead pauses on screen at every approval/rejection (a "次のエポックへ" (Next epoch) button on the run screen advances it).

## 04. Rationality (how consistently the protagonist picks sound moves)

If the genre has `rationality.yaml` (LLM-based rationality judgment / Jev), this section always shows regardless of whether the judgment model is currently reachable. Reachability only affects κ's **default value**. When creating a new config, κ defaults to 0.6 (and the time limit automatically rises to 21600 seconds) if judgment is actually usable, or 0 (off) if not. Raising κ makes irrational moves less likely to be chosen. At 0, no rationality judgment happens at all, and Ollama is never called. When κ is above 0, an estimated run time is shown on screen, figured at roughly 90 seconds per run (including the judge call).

## 05. Route (how hard to rein in detours) { #05 }

Shown when the genre has `route.yaml` (the route layer — see [Route Layer](../concepts/route-layer.md)). Sets **ρ (0–1)**, how strongly the protagonist heads straight for the ending. 0 (the default across the engine, CLI, and template) means the route layer doesn't run at all, unmodulated as before; the closer to 1, the more unreasoned detours are avoided. Detours from body needs or mistaken beliefs are never penalized regardless of ρ. A detour matching the motive table (`motives.yaml`) is instead treated according to the strength of the gene that motive reads — a weak individual gets reined in almost as hard as an unreasoned one.

On the new-config screen, and on the "1-click run" button on the home/world screens for a genre whose template has a `route.yaml`, ρ **defaults to 1.0** (unlike κ, ρ adds no compute cost). Duplicating/editing keeps the saved value as-is. If you switch genres to one whose template has no `route.yaml`, ρ automatically resets to disabled (unset).

## Advanced settings

A collapsible section after 04 (and, when route shows, after 05 too), with these items.

- **ジャンル (Genre)**: only selectable when creating new (fixed to match the source when duplicating)
- **乱数の開始値 (seed_base, default 0)** / **進化の乱数 (ga_seed, default 1)**
- The process count (how many processes run the GA concurrently) is set not here but under **⚙ 全体設定 (Global settings) → 計算 (Compute)** (see [Settings](settings.md))
- **実行時間の上限 (execution_limits.wall_seconds, default 3600 seconds)**: the run is cut off past this many seconds. This default rises to 21600 seconds when using κ (rationality judgment, above)

## Starting a run

Saving takes you to that config's run-condition screen (`/configs/<config ID>`). Clicking "この条件で実行へ →" (Proceed to run with these conditions →) opens the run screen (`/jobs`), and clicking "この設定で GA を回す" (Run the GA with this config) starts it. While running, a per-generation progress bar is shown, and **results through the last completed generation are kept even if you stop the run**. Once a run finishes, you can re-run it from the same screen via "同じ設定でもう一度回す" (Run again with the same config).
