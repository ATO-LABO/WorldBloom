---
ja_rev: "687380ba8e49"
---
# Settings

Open this from **⚙ 全体設定** (Global settings) at the top right of the screen (or `/configs`). Changes here aren't tied to a specific world — they're **shared across every world**. The underlying file is at the repository root when running from source, or `app/settings.json` (inside the `app` folder next to the exe) in the distributed exe build. See [settings.json](../reference/settings-json.md) for the full file spec. The left menu contains AI settings, Computation, and Saved run settings.

## AI settings { #output-settings }

Check the connection used for [world drafts](create-world.md) and [synopsis/prose generation](generate-text.md). The current screen contains “使用するAI” (AI to use), “生成の上限” (Generation limits), and “合理性の判定器（Jev）” (Rationality judge).

The AI tab shows the saved destination and Gemma 4 installation status. “Gemma 4を選ぶ” selects an edit; **the destination changes only after “設定を保存” (Save settings)**. On the PC running Ollama, run `ollama pull gemma4:e4b-it-qat`, then refresh installation status. The status check neither generates text nor downloads a model.

For other supported backends/models, see [LLM Backends](llm-backends.md) and [settings.json](../reference/settings-json.md). A different saved model is not silently switched to Gemma. Gemma uses a model-specific option profile, preserving other models' base settings.

Generation limits let you change the selected backend's AI-call cap, per-call timeout, overall timeout and saved-response size limit. Saved settings apply to newly started generation, not running jobs. Check the saved values, particularly when using a world-generation strategy with multiple model calls.

In the Jev tab, save a TypeSafe API key. Saving first sends one real judging question to validate the key. Experiments using Jev with κ>0 send protagonist situation text to `api.typesafe.ai`. Keys are not displayed on screen or in experiment records. Choose the judge and κ per experiment in [Run Settings](run-settings.md#04).

## Compute

Settings for how much this PC parallelizes GA experiments.

| Item | Contents | Default |
|---|---|---|
| GA の並列数 (GA process count, processes) | How many individual evaluations run at once in the GA. **Only affects speed, never the results** | `min(8, this PC's CPU core count)` |

On a development machine (20 cores), measurements showed speed plateauing around 8 processes. Applies to GA experiments started after the save.

The “GPU ガード” (GPU guard) tab controls temperature checks before local text generation. At the threshold, generation waits for an 8°C drop, for up to ten minutes. See [GPU Guard](gpu-guard.md).


## Saved run-setting versions

A list of previously saved [run settings](run-settings.md). Saved versions can't be edited (use "複製して調整", Duplicate and adjust, to duplicate one if you want different contents). Each entry shows its world, genre, run size (generations × population × seeds), creation time, and duplication source.
